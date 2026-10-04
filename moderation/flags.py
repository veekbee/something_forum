"""Automatic flags (docs/DESIGN.md, Automatic flags): they go into the moderation queue as Report
rows with source "system". At most one open flag of each kind per member; repeats add to its count.

- Rate-limit refusals: flags.rate_limit_refusals posts refused within flags.rate_limit_window_hours.
- Rapid deletions: flags.rapid_deletions of a member's own posts within
  flags.rapid_deletion_window_minutes.
- Request rate: more than scraping.requests_per_10_min requests in a ten-minute window. The account
  is read-only until a Moderator resolves the flag.

Counters for refusals and requests live in the shared cache (settings.CACHES), because neither
leaves a row of its own.
"""

import time
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from audit import log
from core import registry
from moderation.models import Report


@transaction.atomic
def raise_flag(user, kind, details=None, post=None):
    existing = Report.objects.waiting().select_for_update().filter(source=Report.Source.SYSTEM, kind=kind, user=user).first()
    if existing is not None:
        existing.details["repeats"] = existing.details.get("repeats", 0) + 1
        existing.save(update_fields=["details"])
        return existing
    flag = Report.objects.create(
        source=Report.Source.SYSTEM, kind=kind, user=user, post=post, details=details or {}
    )
    log.record(None, "flag.raise", flag, {"kind": kind, "user": user.pk})
    return flag


def record_rate_limit_refusal(user, subforum):
    """Called when a post is refused by a rate limit. Must run outside the refused transaction."""
    window = registry.site_value("flags.rate_limit_window_hours") * 3600
    key = f"flags:refusals:{user.pk}"
    now = time.time()
    stamps = [t for t in cache.get(key, []) if t > now - window] + [now]
    cache.set(key, stamps, timeout=window)
    if len(stamps) >= registry.site_value("flags.rate_limit_refusals"):
        cache.delete(key)
        return raise_flag(user, Report.Kind.FLAG_RATE_LIMIT,
                          {"refusals": len(stamps), "subforum": subforum.pk if subforum else None})
    return None


def check_rapid_deletions(user):
    """Called after a member deletes one of their own posts."""
    from boards.models import Post

    since = timezone.now() - timedelta(minutes=registry.site_value("flags.rapid_deletion_window_minutes"))
    count = Post.objects.filter(author=user, deleted_by=user, deleted_at__gte=since).count()
    if count >= registry.site_value("flags.rapid_deletions"):
        return raise_flag(user, Report.Kind.FLAG_RAPID_DELETION, {"deletions": count})
    return None


def count_request(user):
    """Called once per signed-in request. Returns the flag when the limit is first passed."""
    bucket = int(time.time() // 600)
    key = f"flags:requests:{user.pk}:{bucket}"
    cache.add(key, 0, timeout=1200)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=1200)
        count = 1
    if count == registry.site_value("scraping.requests_per_10_min") + 1:
        return raise_flag(user, Report.Kind.FLAG_REQUEST_RATE, {"requests": count, "window_minutes": 10})
    return None


class RequestRateMiddleware:
    """Counts each signed-in member's requests (design: anti-scraping). Runs after access control,
    so only sessions that passed TOTP are counted."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            count_request(user)
        return self.get_response(request)
