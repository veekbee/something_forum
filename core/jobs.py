"""The job runner (docs/DESIGN.md, Stack; rule 81).

run_jobs ticks every five minutes. Each tick runs the frequent jobs, then any daily job whose last
success is older than today's slot at jobs.daily_hour_utc, in the order below, so a missed day is
caught up on the next tick. Each job runs in its own transaction under a Postgres advisory lock,
so two copies of the runner never run one job at once, and a failure is logged and recorded
without stopping the others. Owners are notified once a job has failed on two runs in a row.
"""

import io
import logging
import traceback
import zlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from typing import Callable

from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from core import registry

logger = logging.getLogger("forum.jobs")

TICK = timedelta(minutes=5)


def _command(name):
    def run():
        out = io.StringIO()
        call_command(name, stdout=out)
        return out.getvalue().strip()

    return run


def _retention():
    from audit import retention

    return retention.run()


@dataclass(frozen=True)
class Job:
    name: str
    run: Callable[[], object]


DAILY = [
    Job("expire_invitations", _command("expire_invitations")),
    Job("delete_ended_accounts", _command("delete_ended_accounts")),
    Job("expire_actions", _command("expire_actions")),
    Job("billing_daily", _command("billing_daily")),
    Job("sessions_daily", _command("sessions_daily")),
    Job("prune_read_positions", _command("prune_read_positions")),
    Job("send_notification_emails", _command("send_notification_emails")),
    Job("retention", _retention),
]

def _data_requests():
    from accounts import data_rights

    built, deleted = data_rights.build_exports(), data_rights.delete_old_exports()
    return f"built {built} export(s), deleted {deleted} old file(s)" if built or deleted else ""


FREQUENT = [Job("data_requests", _data_requests)]


def daily_slot(now):
    """The most recent daily run time at or before now."""
    hour = registry.site_value("jobs.daily_hour_utc")
    utc = now.astimezone(dt_timezone.utc)
    slot = datetime.combine(utc.date(), time(hour), tzinfo=dt_timezone.utc)
    return slot if slot <= utc else slot - timedelta(days=1)


def _last(job, **filters):
    from core.models import JobRun

    return JobRun.objects.filter(job=job, **filters).order_by("-started_at", "-pk").first()


def is_due(job, now):
    last = _last(job.name, ok=True)
    return last is None or last.started_at < daily_slot(now)


def _lock_key(name):
    return zlib.crc32(f"forum.job.{name}".encode())


def run_job(job, now=None):
    """Run one job. Returns True on success, False on failure, None if another copy holds it."""
    from core.models import JobRun

    started = now or timezone.now()
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [_lock_key(job.name)])
                if not cursor.fetchone()[0]:
                    return None
            output = job.run()
            JobRun.objects.create(job=job.name, started_at=started, finished_at=timezone.now(), ok=True)
        if output:
            logger.info("%s: %s", job.name, output)
        return True
    except Exception as exc:
        logger.exception("Job %s failed", job.name)
        _record_failure(job, started, exc)
        return False


def _record_failure(job, started, exc):
    from accounts import roles
    from core.models import JobRun, Notification

    with transaction.atomic():
        previous = _last(job.name)
        failures = (previous.consecutive_failures if previous and not previous.ok else 0) + 1
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:2000]
        JobRun.objects.create(job=job.name, started_at=started, finished_at=timezone.now(), ok=False,
                              error=error, consecutive_failures=failures)
        if failures == 2:
            for owner in roles.owners():
                Notification.objects.create(recipient=owner, kind="jobs.failed", payload={"job": job.name})


def tick(now=None):
    """One pass: the frequent jobs, then the daily jobs that are due, in order."""
    now = now or timezone.now()
    results = {}
    for job in FREQUENT:
        results[job.name] = run_job(job, now)
    for job in DAILY:
        if is_due(job, now):
            results[job.name] = run_job(job, now)
    return results
