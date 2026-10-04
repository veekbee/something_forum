"""A Guest's or Provisional's first N posts site-wide are held; rejected posts are struck from the count."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from boards import services
from core.permissions import can
from tests.factories import make_dm, make_post, make_thread


def _post_n(author, thread, n):
    return [services.reply(author, thread, f"post {i}") for i in range(n)]


def test_first_five_provisional_posts_are_held(make_user, general):
    member = make_user("provisional")
    thread = make_thread(general, make_user("full"))
    posts = _post_n(member, thread, 6)
    assert [p.is_held for p in posts] == [True] * 5 + [False]


def test_count_is_site_wide(make_user, general, lobby):
    member = make_user("provisional")
    _post_n(member, make_thread(lobby, make_user("full")), 3)
    posts = _post_n(member, make_thread(general, make_user("full")), 3)
    assert [p.is_held for p in posts] == [True, True, False]


def test_guest_posts_in_lobby_are_held(make_user, lobby):
    guest = make_user("guest")
    thread, first = services.start_thread(guest, lobby, "Hello", "I'm new here")
    assert first.is_held


def test_rejected_post_is_struck_from_the_count(make_user, general):
    member = make_user("provisional")
    moderator = make_user("moderator")
    thread = make_thread(general, make_user("full"))
    posts = _post_n(member, thread, 5)
    services.reject_post(moderator, posts[0], "off topic")
    # Only four counted posts so far, so the next one is still held.
    assert services.reply(member, thread, "again").is_held
    assert not services.reply(member, thread, "and again").is_held


def test_released_and_deleted_posts_still_count(make_user, general):
    member = make_user("provisional")
    moderator = make_user("moderator")
    thread = make_thread(general, make_user("full"))
    posts = _post_n(member, thread, 5)
    services.release_post(moderator, posts[0])
    posts[1].deleted_at = timezone.now()
    posts[1].save()
    assert not services.reply(member, thread, "sixth").is_held


def test_full_member_is_not_held(make_user, general):
    full = make_user("full")
    assert not services.reply(full, make_thread(general, full), "hi").is_held


def test_subforum_can_override_n(make_user, general):
    general.settings["provisional.held_posts"] = 1
    general.save()
    member = make_user("provisional")
    posts = _post_n(member, make_thread(general, make_user("full")), 2)
    assert [p.is_held for p in posts] == [True, False]


def test_hold_all(make_user, general):
    general.settings["subforum.hold_posts"] = "all"
    general.save()
    tenured = make_user("tenured")
    assert services.reply(tenured, make_thread(general, tenured), "hi").is_held


def test_hold_off(make_user, general):
    general.settings["subforum.hold_posts"] = "off"
    general.save()
    member = make_user("provisional")
    assert not services.reply(member, make_thread(general, make_user("full")), "hi").is_held


def test_dm_posts_are_never_held_and_do_not_count(make_user, general):
    from tests.factories import sponsor as record_sponsorship

    member = make_user("provisional")
    sponsor = make_user("full")
    record_sponsorship(sponsor, member)
    dm = make_dm(member, sponsor)
    for _ in range(5):
        assert not services.reply(member, dm, "hello").is_held
    assert services.reply(member, make_thread(general, sponsor), "first forum post").is_held


def test_held_post_is_visible_to_author_and_approvers_only(make_user, general, serious):
    member = make_user("provisional")
    post = services.reply(member, make_thread(general, make_user("full")), "pending")
    assert not can(make_user("full"), "post.read", post)
    assert can(member, "post.read", post)
    assert can(make_user("moderator"), "post.read", post)
    assert can(make_user("admin"), "post.read", post)
    scoped = make_user("tenured")
    from tests.factories import grant

    grant(scoped, "moderator", scope_subforum=serious)
    assert not can(scoped, "post.read", post)


def test_rejected_post_leaves_the_thread_but_stays_in_author_history(make_user, general):
    from core.models import Notification

    member, other = make_user("provisional"), make_user("full")
    post = services.reply(member, make_thread(general, other), "pending")
    services.reject_post(make_user("moderator"), post, "off topic")
    assert not can(member, "post.read", post)
    assert can(member, "post.read_in_history", post)
    assert not can(other, "post.read_in_history", post)
    assert can(make_user("moderator"), "post.read", post)
    note = Notification.objects.get(recipient=member)
    assert note.kind == "post.rejected" and note.payload == {"post": post.pk, "thread": post.thread_id}


def test_author_history_shows_held_posts_but_not_deleted_ones(make_user, general):
    member = make_user("provisional")
    post = services.reply(member, make_thread(general, make_user("full")), "pending")
    assert can(member, "post.read_in_history", post)
    post.deleted_at = timezone.now()
    post.save()
    assert not can(member, "post.read_in_history", post)


def test_released_post_is_visible(make_user, general):
    member = make_user("provisional")
    post = services.reply(member, make_thread(general, make_user("full")), "pending")
    services.release_post(make_user("moderator"), post)
    assert can(make_user("full"), "post.read", post)


def test_only_staff_in_scope_release(make_user, general, serious):
    from tests.factories import grant

    member = make_user("provisional")
    post = services.reply(member, make_thread(general, make_user("full")), "pending")
    with pytest.raises(PermissionDenied):
        services.release_post(make_user("tenured"), post)
    scoped = make_user("tenured")
    grant(scoped, "moderator", scope_subforum=serious)
    with pytest.raises(PermissionDenied):
        services.release_post(scoped, post)
    grant(scoped, "moderator", scope_subforum=general)
    services.release_post(scoped, post)


def test_member_under_a_hold_action_is_held(make_user, general):
    from moderation import services as moderation

    full = make_user("full")
    moderation.initiate_action(
        make_user("admin"), full, "hold", internal_reason="posting spree", ends_at=timezone.now() + timedelta(days=1)
    )
    assert services.reply(full, make_thread(general, full), "hi").is_held


def test_held_post_counts_toward_rate_limit(make_user, serious):
    member = make_user("provisional")
    make_post(make_thread(serious, member), member, is_held=True)
    assert not can(member, "thread.reply", make_thread(serious, make_user("full")))
