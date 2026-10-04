"""Rolling-window rate limits and held-post rules, computed from Post rows (design rules 6 and 7)."""

from datetime import timedelta

from django.utils import timezone

from accounts import roles
from boards.models import Post, Thread


def post_limit_for(user, subforum):
    """The sub-forum's per-role limit for the user's trust role if one is set, else its general
    post limit. None means unlimited."""
    role = roles.trust_role(user)
    by_role = subforum.setting("subforum.post_rate_limit_by_role")
    if role is not None and role.name in by_role:
        return by_role[role.name]
    return subforum.setting("subforum.post_rate_limit")


def _window_start(limit, now):
    return now - timedelta(hours=limit["window_hours"])


def post_limit_reached(user, subforum, now=None):
    limit = post_limit_for(user, subforum)
    if limit is None:
        return False
    now = now or timezone.now()
    recent = Post.objects.counted().filter(
        author=user, thread__subforum=subforum, created_at__gt=_window_start(limit, now)
    )
    return recent.count() >= limit["count"]


def next_post_at(user, subforum, now=None):
    """When the member may post here again under the rolling window, or None if they may now."""
    limit = post_limit_for(user, subforum)
    if limit is None:
        return None
    now = now or timezone.now()
    recent = list(Post.objects.counted().filter(
        author=user, thread__subforum=subforum, created_at__gt=_window_start(limit, now)
    ).order_by("-created_at").values_list("created_at", flat=True)[:limit["count"]])
    if len(recent) < limit["count"]:
        return None
    return recent[-1] + timedelta(hours=limit["window_hours"])


def describe_limit(limit):
    """A sub-forum's post limit in words, for the reply form (docs/DESIGN.md, Visual design)."""
    if limit is None:
        return ""
    count, hours = limit["count"], limit["window_hours"]
    spans = {24: "day", 168: "week", 1: "hour"}
    span = spans.get(hours, f"{hours} hours")
    per = f"per {span}" if hours in spans else f"every {span}"
    return f"{count} post{'s' if count != 1 else ''} per member {per}"


def thread_limit_reached(user, subforum, now=None):
    limit = subforum.setting("subforum.thread_rate_limit")
    if limit is None:
        return False
    now = now or timezone.now()
    # A thread whose opening post was rejected (and has no other counted post by its author) is
    # not counted, matching the rule for posts.
    recent = Thread.objects.filter(
        author=user,
        subforum=subforum,
        created_at__gt=_window_start(limit, now),
        posts__author=user,
        posts__rejected_at__isnull=True,
    ).distinct()
    return recent.count() >= limit["count"]


def should_hold(user, subforum):
    """Whether a new post by `user` in `subforum` is held for review. DMs are never held."""
    from moderation.models import ModerationAction

    if subforum is None:
        return False
    if roles.moderates(user, subforum):
        return False
    holds = ModerationAction.objects.in_force().applying_in(subforum)
    if holds.filter(target_user=user, kind=ModerationAction.Kind.HOLD).exists():
        return True
    mode = subforum.setting("subforum.hold_posts")
    if mode == "all":
        return True
    if mode == "first_n_provisional":
        role = roles.trust_role(user)
        if role is not None and role.name in (roles.GUEST, roles.PROVISIONAL):
            n = subforum.setting("provisional.held_posts")
            posted = Post.objects.counted().filter(author=user, thread__kind=Thread.Kind.DISCUSSION).count()
            return posted < n
    return False
