"""Build step 9: the job runner (rule 81)."""

from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from django.core.management import call_command
from django.db import connection

from core import jobs
from core.models import JobRun, Notification, SiteSetting

ROOT = Path(__file__).resolve().parent.parent


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=dt_timezone.utc)


@pytest.fixture
def fake(monkeypatch, seeded):
    """Replace the job lists with recording fakes; fake.fail names jobs that raise."""
    ran, state = [], SimpleNamespace(fail=set())

    def make(name):
        def run():
            ran.append(name)
            if name in state.fail:
                raise RuntimeError(f"{name} broke")
        return jobs.Job(name, run)

    daily = [make(n) for n in ("first", "second", "third")]
    monkeypatch.setattr(jobs, "DAILY", daily)
    monkeypatch.setattr(jobs, "FREQUENT", [make("often")])
    state.ran = ran
    return state


def test_daily_jobs_run_in_order_once_a_day_at_the_hour(fake):
    jobs.tick(at(10, 3, 1))
    assert fake.ran == ["often", "first", "second", "third"]
    fake.ran.clear()
    jobs.tick(at(10, 23))
    jobs.tick(at(11, 2, 55))  # before the next slot
    assert fake.ran == ["often", "often"]
    fake.ran.clear()
    jobs.tick(at(11, 3, 0))
    assert fake.ran == ["often", "first", "second", "third"]


def test_the_hour_is_a_setting(fake, owner):
    SiteSetting.objects.create(key="jobs.daily_hour_utc", value=22, updated_by=owner)
    jobs.tick(at(10, 22, 30))
    fake.ran.clear()
    jobs.tick(at(11, 21, 55))
    assert fake.ran == ["often"]
    jobs.tick(at(11, 22, 0))
    assert fake.ran[-3:] == ["first", "second", "third"]


def test_a_missed_day_is_caught_up_on_the_next_tick(fake):
    jobs.tick(at(10, 3))
    fake.ran.clear()
    jobs.tick(at(13, 14))  # the runner was down for days
    assert fake.ran == ["often", "first", "second", "third"]


def test_one_failure_does_not_stop_the_others_and_rolls_back(fake, make_user):
    from boards.models import SubForum

    def breaks_after_writing():
        SubForum.objects.filter(slug="general-discussion").update(name="Changed")
        raise RuntimeError("halfway")

    jobs.DAILY[1] = jobs.Job("second", breaks_after_writing)
    results = jobs.tick(at(10, 3))
    assert results == {"often": True, "first": True, "second": False, "third": True}
    assert SubForum.objects.get(slug="general-discussion").name != "Changed"
    run = JobRun.objects.get(job="second")
    assert not run.ok and "halfway" in run.error and run.consecutive_failures == 1


def test_owners_hear_once_a_job_fails_twice_in_a_row(fake, owner, make_user):
    second_owner = make_user("owner")
    fake.fail = {"second"}
    jobs.tick(at(10, 3))
    assert not Notification.objects.filter(kind="jobs.failed").exists()
    jobs.tick(at(10, 3, 5))  # a failed daily job is retried on the next tick
    assert set(Notification.objects.filter(kind="jobs.failed").values_list("recipient", flat=True)) == {
        owner.pk, second_owner.pk}
    jobs.tick(at(10, 3, 10))
    assert Notification.objects.filter(kind="jobs.failed").count() == 2  # not again for a third
    assert JobRun.objects.filter(job="second").latest("pk").consecutive_failures == 3
    fake.fail = set()
    jobs.tick(at(10, 3, 15))
    assert JobRun.objects.filter(job="second").latest("pk").ok
    fake.fail = {"second"}
    jobs.tick(at(11, 3))
    assert JobRun.objects.filter(job="second").latest("pk").consecutive_failures == 1


def test_frequent_failures_count_too(fake, owner):
    fake.fail = {"often"}
    jobs.tick(at(10, 3))
    jobs.tick(at(10, 3, 5))
    assert Notification.objects.get(kind="jobs.failed").payload == {"job": "often"}


def test_the_notification_is_an_account_kind_with_a_pointer_email(fake, owner, mailoutbox,
                                                                   django_capture_on_commit_callbacks):
    fake.fail = {"first"}
    with django_capture_on_commit_callbacks(execute=True):
        jobs.tick(at(10, 3))
        jobs.tick(at(10, 3, 5))
    assert [m.to for m in mailoutbox] == [[owner.email]]
    assert "first" not in mailoutbox[0].body


def test_a_job_another_copy_holds_is_skipped(fake):
    params = connection.get_connection_params()
    other = psycopg.connect(dbname=params["dbname"], user=params.get("user"), password=params.get("password"),
                            host=params.get("host"), port=params.get("port"))
    try:
        other.execute("SELECT pg_advisory_lock(%s)", [jobs._lock_key("first")])
        results = jobs.tick(at(10, 3))
        assert results["first"] is None and results["second"] is True
        assert not JobRun.objects.filter(job="first").exists()
    finally:
        other.close()
    assert jobs.tick(at(10, 3, 5))["first"] is True


def test_the_real_jobs_in_the_designed_order(seeded):
    assert [job.name for job in jobs.DAILY] == [
        "expire_invitations", "delete_ended_accounts", "expire_actions", "billing_daily",
        "sessions_daily", "prune_read_positions", "send_notification_emails"]
    results = jobs.tick()
    assert all(results[job.name] for job in jobs.DAILY)


def test_command_runs_one_tick(fake):
    from io import StringIO

    out = StringIO()
    call_command("run_jobs", "--once", stdout=out)
    assert "first: ok" in out.getvalue()


def test_compose_files_run_the_jobs_service():
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        text = (ROOT / name).read_text()
        assert "\n  jobs:\n" in text and "manage.py run_jobs" in text
    prod = (ROOT / "docker-compose.prod.yml").read_text()
    assert "gunicorn" in prod and ".:/app" not in prod and "runserver" not in prod
