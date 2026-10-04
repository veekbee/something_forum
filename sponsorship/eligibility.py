"""Promotion eligibility is computed, never stored. The system shows it; people decide."""

from datetime import timedelta

from django.utils import timezone

from accounts import roles
from accounts.models import RoleAssignment
from boards.models import Post, Thread
from core import registry


def current_assignment(member, role_name):
    return (
        RoleAssignment.objects.filter(
            user=member, role__name=role_name, revoked_at__isnull=True, scope_subforum__isnull=True
        )
        .order_by("granted_at")
        .first()
    )


def paused_since(member, since, now=None):
    """Time since `since` during which the Provisional clock was paused: lapsed (rule 44) or
    waiting for a new sponsor (rule 51). Overlapping periods count once."""
    from billing.models import LapsePeriod
    from sponsorship.transfers import waiting_periods

    now = now or timezone.now()
    periods = list(LapsePeriod.objects.filter(user=member).values_list("started_at", "ended_at"))
    periods += waiting_periods(member)
    spans = sorted((max(start, since), min(end or now, now)) for start, end in periods)
    total, reached = timedelta(0), since
    for start, end in spans:
        start = max(start, reached)
        if end > start:
            total += end - start
            reached = end
    return total


def full_promotion_eligible(member, now=None):
    """Provisional for at least promotion.full.min_days with at least promotion.full.min_posts
    counted forum posts made since becoming Provisional."""
    now = now or timezone.now()
    role = roles.trust_role(member)
    if role is None or role.name != roles.PROVISIONAL:
        return False
    since = current_assignment(member, roles.PROVISIONAL).granted_at
    if now - since - paused_since(member, since, now) < timedelta(days=registry.site_value("promotion.full.min_days")):
        return False
    posts = Post.objects.counted().filter(
        author=member, thread__kind=Thread.Kind.DISCUSSION, created_at__gte=since
    )
    return posts.count() >= registry.site_value("promotion.full.min_posts")


def tenured_promotion_eligible(member, now=None):
    """Full for at least promotion.tenured.min_days. Good standing is for the promoter to judge."""
    now = now or timezone.now()
    role = roles.trust_role(member)
    if role is None or role.name != roles.FULL:
        return False
    since = current_assignment(member, roles.FULL).granted_at
    return now - since >= timedelta(days=registry.site_value("promotion.tenured.min_days"))
