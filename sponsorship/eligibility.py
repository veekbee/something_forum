"""Promotion eligibility is computed, never stored. The system shows it; people decide."""

from datetime import timedelta

from django.utils import timezone

from accounts import roles
from accounts.models import RoleAssignment
from boards.models import Post, Thread
from core import registry


def _current_assignment(member, role_name):
    return (
        RoleAssignment.objects.filter(
            user=member, role__name=role_name, revoked_at__isnull=True, scope_subforum__isnull=True
        )
        .order_by("granted_at")
        .first()
    )


def full_promotion_eligible(member, now=None):
    """Provisional for at least promotion.full.min_days with at least promotion.full.min_posts
    counted forum posts made since becoming Provisional."""
    now = now or timezone.now()
    role = roles.trust_role(member)
    if role is None or role.name != roles.PROVISIONAL:
        return False
    since = _current_assignment(member, roles.PROVISIONAL).granted_at
    if now - since < timedelta(days=registry.site_value("promotion.full.min_days")):
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
    since = _current_assignment(member, roles.FULL).granted_at
    return now - since >= timedelta(days=registry.site_value("promotion.tenured.min_days"))
