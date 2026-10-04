"""Session binding (rules 57 to 59), with a small fixture country database and no network calls."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.test import Client, RequestFactory
from django.utils import timezone

from accounts import sessions
from accounts.models import UserSession
from audit.models import AuditEntry
from core.models import Notification
from core.permissions import can
from moderation import queue
from moderation.models import Report
from tests.factories import enrol_totp

AU, CA, NZ = "203.0.113.9", "198.51.100.7", "2001:db8:1:2::5"
MAC_SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0 Mobile/15E148 Safari/604.1"
WINDOWS_EDGE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 Edg/126.0"


@pytest.fixture(autouse=True)
def geo(tmp_path, settings):
    from mmdb_writer import MMDBWriter
    from netaddr import IPSet

    writer = MMDBWriter(ip_version=6, database_type="Test-Country", ipv4_compatible=True)
    writer.insert_network(IPSet(["203.0.113.0/24"]), {"country": {"iso_code": "AU"}})
    writer.insert_network(IPSet(["198.51.100.0/24"]), {"country": {"iso_code": "CA"}})
    writer.insert_network(IPSet(["2001:db8:1::/48"]), {"country": {"iso_code": "NZ"}})
    path = tmp_path / "country.mmdb"
    writer.to_db_file(str(path))
    settings.GEOIP_COUNTRY_DATABASE = str(path)
    sessions._reader.cache_clear()
    yield
    sessions._reader.cache_clear()


def sign_in(user, ip, device, user_agent=MAC_SAFARI):
    """A completed sign-in from `ip` on `device`; returns a client holding that session, and its record."""
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    store[HASH_SESSION_KEY] = user.get_session_auth_hash()
    store.save()
    request = RequestFactory().get("/", REMOTE_ADDR=ip, HTTP_USER_AGENT=user_agent)
    request.session, request.device_id = store, device
    record = sessions.start(request, user)
    client = Client()
    client.cookies[settings.SESSION_COOKIE_NAME] = store.session_key
    client.cookies[settings.DEVICE_COOKIE_NAME] = device
    return client, record


@pytest.fixture
def member(make_user):
    user = make_user("full")
    enrol_totp(user)
    return user


# --- what is recorded ------------------------------------------------------------------------


def test_only_the_prefix_and_country_are_stored(member):
    _, v4 = sign_in(member, AU, "dev-a")
    _, v6 = sign_in(member, NZ, "dev-a", IPHONE)
    assert (v4.ip_prefix, v4.country) == ("203.0.113.0/24", "AU")
    assert (v6.ip_prefix, v6.country) == ("2001:db8:1::/48", "NZ")
    assert "203.0.113.9" not in str(UserSession.objects.values_list())
    assert (v4.browser_family, v4.os_family) == ("Safari", "macOS")
    assert (v6.browser_family, v6.os_family) == ("Chrome", "iOS")
    assert sessions.families(WINDOWS_EDGE) == ("Edge", "Windows")


def test_unknown_place_and_no_database_mean_no_country(member, settings):
    _, record = sign_in(member, "192.0.2.1", "dev-a")
    assert record.country == ""
    settings.GEOIP_COUNTRY_DATABASE = ""
    assert sessions.country_for(AU) == ""


def test_device_cookie_is_set_once_and_http_only(client, seeded):
    response = client.get("/accounts/login/")
    cookie = response.cookies[settings.DEVICE_COOKIE_NAME]
    assert cookie.value and cookie["httponly"]
    assert settings.DEVICE_COOKIE_NAME not in client.get("/accounts/login/").cookies


# --- concurrent locations --------------------------------------------------------------------


def test_one_device_changing_country_is_not_flagged(member):
    sign_in(member, AU, "dev-a")
    _, travelling = sign_in(member, CA, "dev-a")
    assert travelling.revoked_at is None
    assert not Report.objects.filter(kind="flag_concurrent_location").exists()


def test_two_devices_in_one_country_are_fine(member):
    sign_in(member, AU, "dev-a")
    _, second = sign_in(member, "203.0.113.200", "dev-b", IPHONE)
    assert second.revoked_at is None


def test_two_devices_in_two_countries_end_the_newer_session(member):
    older_client, older = sign_in(member, AU, "dev-a")
    newer_client, newer = sign_in(member, CA, "dev-b", IPHONE)
    newer.refresh_from_db()
    assert newer.revoke_reason == "concurrent_location"
    # The newer device must sign in again and is told why; the older carries on.
    response = newer_client.get("/", follow=True)
    assert response.redirect_chain[0][0].startswith("/accounts/login/")
    assert b"two countries at the same time" in response.content
    assert older_client.get("/").status_code == 200
    older.refresh_from_db()
    assert older.revoked_at is None
    # Told by account email too, and the older device sees the notice.
    assert Notification.objects.filter(recipient=member, kind="session.concurrent_location").exists()
    flag = Report.objects.get(kind="flag_concurrent_location", user=member)
    assert sorted(flag.details["sessions"]) == sorted([older.pk, newer.pk])
    assert AuditEntry.objects.filter(action="session.concurrent_location").exists()


def test_repeats_within_a_day_join_the_same_flag(member):
    sign_in(member, AU, "dev-a")
    sign_in(member, CA, "dev-b")
    sign_in(member, CA, "dev-c")
    flag = Report.objects.get(kind="flag_concurrent_location")
    assert flag.details["repeats"] == 1 and len(flag.details["sessions"]) == 3
    Report.objects.filter(pk=flag.pk).update(created_at=timezone.now() - timedelta(hours=25))
    sign_in(member, CA, "dev-d")
    assert Report.objects.filter(kind="flag_concurrent_location").count() == 2


def test_an_older_session_outside_the_window_does_not_count(member):
    _, older = sign_in(member, AU, "dev-a")
    UserSession.objects.filter(pk=older.pk).update(last_seen_at=timezone.now() - timedelta(minutes=31))
    _, newer = sign_in(member, CA, "dev-b")
    assert newer.revoked_at is None


def test_concurrent_location_flags_are_for_admins_and_owners(member, make_user):
    sign_in(member, AU, "dev-a")
    sign_in(member, CA, "dev-b")
    flag = Report.objects.get(kind="flag_concurrent_location")
    for role in ("moderator", "admin", "owner"):
        staff = make_user(role)
        visible = can(staff, "report.view", flag)
        assert bool(visible) == (role != "moderator")
        assert any(i.obj == flag for i in queue.items(staff)) == (role != "moderator")


def test_nothing_else_happens_to_the_account(member):
    sign_in(member, AU, "dev-a")
    sign_in(member, CA, "dev-b")
    member.refresh_from_db()
    assert member.status == "active" and not member.moderation_actions.exists()


# --- lifetimes -------------------------------------------------------------------------------


def test_lifetimes_differ_for_admins_and_owners(member, make_user):
    _, own = sign_in(member, AU, "dev-a")
    assert own.expires_at - own.created_at == timedelta(days=30)
    _, admin = sign_in(make_user("admin"), AU, "dev-b")
    assert admin.expires_at - admin.created_at == timedelta(days=7)


@pytest.mark.parametrize("role,idle_days,ends", [("full", 13, False), ("full", 15, True),
                                                  ("admin", 0.5, False), ("admin", 2, True)])
def test_idle_sessions_end(make_user, role, idle_days, ends):
    user = make_user(role)
    enrol_totp(user)
    client, record = sign_in(user, AU, "dev-a")
    UserSession.objects.filter(pk=record.pk).update(last_seen_at=timezone.now() - timedelta(days=idle_days))
    response = client.get("/")
    assert (response.status_code == 302) == ends
    record.refresh_from_db()
    assert (record.revoke_reason == "idle") == ends


def test_expired_sessions_end(member):
    client, record = sign_in(member, AU, "dev-a")
    UserSession.objects.filter(pk=record.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get("/").status_code == 302
    record.refresh_from_db()
    assert record.revoke_reason == "expired"


def test_activity_is_recorded(member):
    client, record = sign_in(member, AU, "dev-a")
    UserSession.objects.filter(pk=record.pk).update(last_seen_at=timezone.now() - timedelta(hours=1))
    client.get("/")
    record.refresh_from_db()
    assert timezone.now() - record.last_seen_at < timedelta(minutes=1)


def test_signing_out_marks_the_record(member):
    client, record = sign_in(member, AU, "dev-a")
    client.post("/accounts/logout/")
    record.refresh_from_db()
    assert record.revoke_reason == "signed_out" and record.revoked_at is not None


# --- the member's sessions page --------------------------------------------------------------


def test_members_see_and_sign_out_their_sessions(member, make_user):
    here, mine = sign_in(member, AU, "dev-a")
    phone, other = sign_in(member, "203.0.113.50", "dev-b", IPHONE)
    other_member = make_user("full")
    enrol_totp(other_member)
    stranger_client, stranger = sign_in(other_member, AU, "dev-c")
    page = here.get("/sessions/").content
    assert b"Safari on macOS" in page and b"Chrome on iOS" in page and b"This device" in page
    assert here.post("/sessions/", {"session": stranger.pk}).status_code == 404
    here.post("/sessions/", {"session": mine.pk})  # the current session is not signed out this way
    mine.refresh_from_db()
    assert mine.revoked_at is None
    here.post("/sessions/", {"session": other.pk})
    other.refresh_from_db()
    assert other.revoke_reason == "signed_out_by_member"
    assert phone.get("/").status_code == 302
    assert stranger_client.get("/").status_code == 200


def test_sign_out_every_other_session(member):
    here, _ = sign_in(member, AU, "dev-a")
    second, _ = sign_in(member, AU, "dev-b")
    third, _ = sign_in(member, AU, "dev-c")
    here.post("/sessions/", {"all_others": "1"})
    assert here.get("/").status_code == 200
    assert second.get("/").status_code == 302 and third.get("/").status_code == 302


# --- retention -------------------------------------------------------------------------------


def test_retention_job_deletes_old_records_and_keeps_flags(member):
    sign_in(member, AU, "dev-a")
    _, newer = sign_in(member, CA, "dev-b")
    flag = Report.objects.get(kind="flag_concurrent_location")
    _, recent = sign_in(member, AU, "dev-a")
    UserSession.objects.filter(pk=newer.pk).update(revoked_at=timezone.now() - timedelta(days=91))
    UserSession.objects.filter(pk=recent.pk).update(revoked_at=timezone.now() - timedelta(days=10))
    call_command("sessions_daily", stdout=__import__("io").StringIO())
    assert not UserSession.objects.filter(pk=newer.pk).exists()
    assert UserSession.objects.filter(pk=recent.pk).exists()
    flag.refresh_from_db()
    assert newer.pk in flag.details["sessions"]
    assert AuditEntry.objects.filter(action="session.concurrent_location").exists()


def test_daily_job_ends_idle_sessions(member):
    _, record = sign_in(member, AU, "dev-a")
    UserSession.objects.filter(pk=record.pk).update(last_seen_at=timezone.now() - timedelta(days=20))
    assert sessions.run_daily()["ended"] == 1
    record.refresh_from_db()
    assert record.revoke_reason == "idle"
    assert not SessionStore().exists(record.session_key)


def test_deleting_an_ended_invited_account_takes_its_sessions(make_user):
    """Rule 19's deletion job must not be blocked by session records."""
    from sponsorship import onboarding
    from sponsorship.models import Invitation
    from tests.factories import accepted

    invitation = accepted(make_user("full"))
    invitee = invitation.invitee
    _, record = sign_in(invitee, AU, "dev-a")
    onboarding.rescind(invitation.sponsor, invitation)
    Invitation.objects.filter(pk=invitation.pk).update(decided_at=timezone.now() - timedelta(days=31))
    call_command("delete_ended_accounts", stdout=__import__("io").StringIO())
    assert not UserSession.objects.filter(pk=record.pk).exists()
    from accounts.models import User

    assert not User.objects.filter(pk=invitee.pk).exists()
