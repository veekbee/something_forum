"""Rolling-window rate limits computed from Post rows; thread starts count as posts."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.permissions import can
from tests.factories import make_post, make_thread


def _thread(subforum, make_user):
    return make_thread(subforum, make_user("full"))


@pytest.mark.parametrize(
    "slug, window",
    [("serious-discussion", timedelta(hours=24)), ("seminars", timedelta(days=7))],
)
def test_rolling_window(make_user, subforums, slug, window):
    subforum = subforums[slug]
    member = make_user("full")
    thread = _thread(subforum, make_user)

    assert can(member, "thread.reply", thread)
    earlier = make_post(thread, member, ago=window - timedelta(minutes=1))
    assert not can(member, "thread.reply", thread)
    assert not can(member, "subforum.start_thread", subforum)

    # The window rolls from the earlier post, not from a calendar boundary.
    earlier.created_at = timezone.now() - window - timedelta(minutes=1)
    earlier.save()
    assert can(member, "thread.reply", thread)


@pytest.mark.parametrize("slug", ["general-discussion", "guest-lobby"])
def test_unlimited_subforums(make_user, subforums, slug):
    subforum = subforums[slug]
    member = make_user("full")
    thread = _thread(subforum, make_user)
    for _ in range(20):
        make_post(thread, member)
    assert can(member, "thread.reply", thread)
    assert can(member, "subforum.start_thread", subforum)


def test_thread_start_counts_as_post(make_user, serious):
    from boards import services

    member = make_user("full")
    services.start_thread(member, serious, "Considered", "A considered thought.")
    assert not can(member, "thread.reply", _thread(serious, make_user))


def test_limit_is_per_subforum(make_user, serious, seminars):
    member = make_user("full")
    make_post(_thread(serious, make_user), member)
    assert can(member, "thread.reply", _thread(seminars, make_user))


def test_limit_is_per_member(make_user, serious):
    thread = _thread(serious, make_user)
    make_post(thread, make_user("full"))
    assert can(make_user("full"), "thread.reply", thread)


def test_held_post_counts_rejected_does_not(make_user, serious):
    member = make_user("provisional")
    thread = _thread(serious, make_user)
    post = make_post(thread, member, is_held=True)
    assert not can(member, "thread.reply", thread)
    post.is_held, post.rejected_at = False, timezone.now()
    post.save()
    assert can(member, "thread.reply", thread)


def test_deleted_post_still_counts(make_user, serious):
    member = make_user("full")
    thread = _thread(serious, make_user)
    make_post(thread, member, deleted_at=timezone.now())
    assert not can(member, "thread.reply", thread)


def test_per_role_override_replaces_general_limit(make_user, serious):
    serious.settings["subforum.post_rate_limit_by_role"] = {
        "provisional": {"count": 3, "window_hours": 24},
        "admin": None,
    }
    serious.save()
    thread = _thread(serious, make_user)

    provisional = make_user("provisional")
    make_post(thread, provisional)
    make_post(thread, provisional)
    assert can(provisional, "thread.reply", thread)
    make_post(thread, provisional)
    assert not can(provisional, "thread.reply", thread)

    admin = make_user("admin")
    for _ in range(5):
        make_post(thread, admin)
    assert can(admin, "thread.reply", thread)

    full = make_user("full")
    make_post(thread, full)
    assert not can(full, "thread.reply", thread)


def test_no_per_role_overrides_at_launch(serious, seminars, lobby, general):
    for subforum in (serious, seminars, lobby, general):
        assert subforum.setting("subforum.post_rate_limit_by_role") == {}


def test_thread_rate_limit(make_user, general):
    general.settings["subforum.thread_rate_limit"] = {"count": 2, "window_hours": 24}
    general.save()
    member = make_user("full")
    for _ in range(2):
        make_post(make_thread(general, member), member)
    assert not can(member, "subforum.start_thread", general)
    assert can(member, "thread.reply", make_thread(general, make_user("full")))


def test_thread_with_rejected_opening_post_does_not_count(make_user, general):
    general.settings["subforum.thread_rate_limit"] = {"count": 1, "window_hours": 24}
    general.save()
    member = make_user("provisional")
    make_post(make_thread(general, member), member, rejected_at=timezone.now())
    assert can(member, "subforum.start_thread", general)
