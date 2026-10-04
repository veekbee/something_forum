"""Session binding (docs/DESIGN.md, Against members scraping or sharing credentials; rules 57 to 59).

A device is the browser and system families from the user-agent header plus a random identifier in a
long-lived cookie; there is no client-side fingerprinting. Location is the country, looked up at
sign-in in a database on this server, and only the /24 or /48 prefix of the address is kept.

Two different devices on one account, both active within session.concurrency_window_minutes, in two
different countries, end the newer session: it must sign in again with password and authenticator,
the member is told on both devices and by account email, and one concurrent-location flag per 24
hours goes to Admins and Owners. Nothing else happens automatically."""

import functools
import ipaddress
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.db import transaction
from django.utils import timezone

from accounts import roles
from accounts.models import UserSession
from audit import log
from core import registry
from core.models import Notification

Reason = UserSession.RevokeReason

CONCURRENT_MESSAGE = ("Your account was in use from two countries at the same time, so we asked the newer device "
                      "to sign in again. If that wasn't you, change your password.")
ENDED_MESSAGES = {
    Reason.CONCURRENT_LOCATION: CONCURRENT_MESSAGE,
    Reason.SIGNED_OUT_BY_MEMBER: "This session was signed out from your sessions page. Please sign in again.",
    Reason.FACTOR_RESET: "Your authenticator was reset. Sign in again and set up a new one.",
    Reason.EXPIRED: "Your session reached its time limit. Please sign in again.",
    Reason.IDLE: "Your session ended after a time unused. Please sign in again.",
}


# --- device and place ----------------------------------------------------------------------

_BROWSERS = (  # order matters: Edge and Opera also say Chrome, Chrome also says Safari
    ("Edge", r"Edg(e|A|iOS)?/"), ("Opera", r"OPR/|Opera"), ("Samsung Internet", r"SamsungBrowser/"),
    ("Firefox", r"Firefox/|FxiOS/"), ("Chrome", r"Chrome/|CriOS/"), ("Safari", r"Safari/"),
)
_SYSTEMS = (
    ("iOS", r"iPhone|iPad|iPod"), ("Android", r"Android"), ("ChromeOS", r"CrOS"),
    ("Windows", r"Windows"), ("macOS", r"Macintosh|Mac OS X"), ("Linux", r"Linux"),
)


def families(user_agent):
    """Browser and operating-system families, or "Other"."""
    browser = next((name for name, pattern in _BROWSERS if re.search(pattern, user_agent or "")), "Other")
    system = next((name for name, pattern in _SYSTEMS if re.search(pattern, user_agent or "")), "Other")
    return browser, system


def ip_prefix(address):
    """The /24 (IPv4) or /48 (IPv6) network; the full address is never stored."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return ""
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    length = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{length}", strict=False))


@functools.lru_cache(maxsize=4)
def _reader(path):
    import maxminddb

    return maxminddb.open_database(path)


def country_for(address):
    """ISO country code from the local database, or "" when unknown. Never calls another service."""
    path = settings.GEOIP_COUNTRY_DATABASE
    if not path or not address:
        return ""
    try:
        record = _reader(path).get(address)
    except (ValueError, OSError):
        return ""
    return ((record or {}).get("country") or {}).get("iso_code", "") or ""


def client_address(request):
    return request.META.get("REMOTE_ADDR", "")


def new_device_id():
    return secrets.token_urlsafe(24)


# --- lifetimes -----------------------------------------------------------------------------


def _staff(user):
    return roles.is_admin_or_owner(user)


def lifetime(user):
    key = "session.staff_lifetime_days" if _staff(user) else "session.member_lifetime_days"
    return timedelta(days=registry.site_value(key))


def idle_limit(user):
    key = "session.staff_idle_days" if _staff(user) else "session.member_idle_days"
    return timedelta(days=registry.site_value(key))


def ended_reason(record, now=None):
    """Why this session can no longer be used, or None while it is good."""
    now = now or timezone.now()
    if record.revoked_at is not None:
        return record.revoke_reason or Reason.SIGNED_OUT
    if record.expires_at is not None and now >= record.expires_at:
        return Reason.EXPIRED
    if now - record.last_seen_at >= idle_limit(record.user):
        return Reason.IDLE
    return None


def _session_store(key):
    from importlib import import_module

    return import_module(settings.SESSION_ENGINE).SessionStore(session_key=key)


def end(record, reason, now=None, delete_session=True):
    """Revoke the record and delete the Django session behind it, so the device must sign in again.
    During the request that owns the session, leave the session alone: the binding middleware signs
    it out on the next request."""
    if record.revoked_at is None:
        record.revoked_at, record.revoke_reason = now or timezone.now(), reason
        record.save(update_fields=["revoked_at", "revoke_reason"])
    if delete_session and record.session_key:
        _session_store(record.session_key).delete()


# --- sign-in -------------------------------------------------------------------------------


def start(request, user, check=True):
    """Record a new session at sign-in, then check for a second concurrent location. A session
    found without a record (one that began before session binding) is recorded without the check."""
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key
    address = client_address(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    browser, system = families(user_agent)
    now = timezone.now()
    with transaction.atomic():
        record, _ = UserSession.objects.update_or_create(session_key=session_key, defaults={
            "user": user, "device_id": getattr(request, "device_id", "") or "",
            "browser_family": browser, "os_family": system, "ip_prefix": ip_prefix(address),
            "country": country_for(address), "user_agent": user_agent[:500], "created_at": now,
            "last_seen_at": now, "expires_at": now + lifetime(user), "revoked_at": None, "revoke_reason": "",
            "watermark_seed": secrets.token_hex(4),
        })
        if check:
            _check_concurrent(record, now)
    return record


def _check_concurrent(record, now):
    if not record.country or not record.device_id:
        return None
    since = now - timedelta(minutes=registry.site_value("session.concurrency_window_minutes"))
    others = list(UserSession.objects.filter(
        user=record.user, revoked_at__isnull=True, last_seen_at__gte=since
    ).exclude(pk=record.pk).exclude(device_id="").exclude(device_id=record.device_id).exclude(country="").exclude(
        country=record.country))
    if not others:
        return None
    end(record, Reason.CONCURRENT_LOCATION, now, delete_session=False)
    ids = [record.pk, *(o.pk for o in others)]
    log.record(None, "session.concurrent_location", record.user, {"sessions": ids})
    Notification.objects.create(recipient=record.user, kind="session.concurrent_location", payload={})
    return _flag(record.user, ids, now)


def _flag(user, session_ids, now):
    """One flag per 24 hours: repeats are added to it. Details hold session ids only."""
    from moderation.models import Report

    kind = Report.Kind.FLAG_CONCURRENT_LOCATION
    flag = Report.objects.select_for_update().filter(
        kind=kind, user=user, source=Report.Source.SYSTEM, created_at__gte=now - timedelta(hours=24)
    ).order_by("-created_at").first()
    if flag is not None:
        flag.details["sessions"] = sorted(set(flag.details.get("sessions", [])) | set(session_ids))
        flag.details["repeats"] = flag.details.get("repeats", 0) + 1
        flag.save(update_fields=["details"])
        return flag
    flag = Report.objects.create(source=Report.Source.SYSTEM, kind=kind, user=user, details={"sessions": session_ids})
    log.record(None, "flag.raise", flag, {"kind": kind, "user": user.pk})
    return flag


def on_logged_in(sender, request, user, **kwargs):
    if request is not None and isinstance(getattr(request, "session", None), SessionBase):
        start(request, user)


def on_logged_out(sender, request, user, **kwargs):
    key = getattr(getattr(request, "session", None), "session_key", None)
    if key:
        UserSession.objects.filter(session_key=key, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoke_reason=Reason.SIGNED_OUT)


# --- members' own sessions -----------------------------------------------------------------


def active_sessions(user, now=None):
    now = now or timezone.now()
    return [s for s in UserSession.objects.filter(user=user, revoked_at__isnull=True).order_by("-last_seen_at")
            if ended_reason(s, now) is None]


@transaction.atomic
def sign_out(user, record, current_key):
    """A member signs out one of their own sessions (not the one they are using)."""
    if record.user_id != user.pk or record.session_key == current_key:
        return False
    end(record, Reason.SIGNED_OUT_BY_MEMBER)
    log.record(user, "session.sign_out", user, {"session": record.pk})
    return True


@transaction.atomic
def sign_out_others(user, current_key):
    count = 0
    for record in UserSession.objects.filter(user=user, revoked_at__isnull=True).exclude(session_key=current_key):
        end(record, Reason.SIGNED_OUT_BY_MEMBER)
        count += 1
    log.record(user, "session.sign_out_others", user, {"sessions": count})
    return count


def end_all(user, reason, actor=None):
    """Every session of the account, for a second-factor reset (rule 63)."""
    now = timezone.now()
    for record in UserSession.objects.filter(user=user, revoked_at__isnull=True):
        end(record, reason, now)


# --- the daily job -------------------------------------------------------------------------


def run_daily(now=None):
    """End sessions past their lifetime or idle limit, then delete records session.retention_days
    after their session ended (rule 59). Flags and audit entries keep only the ids."""
    now = now or timezone.now()
    ended = 0
    for record in UserSession.objects.filter(revoked_at__isnull=True).select_related("user"):
        reason = ended_reason(record, now)
        if reason is not None:
            end(record, reason, now)
            ended += 1
    cutoff = now - timedelta(days=registry.site_value("session.retention_days"))
    deleted, _ = UserSession.objects.filter(revoked_at__isnull=False, revoked_at__lt=cutoff).delete()
    return {"ended": ended, "deleted": deleted}
