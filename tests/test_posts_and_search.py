"""Build step 3: editing with revision history, soft delete, thread states, visibility and search."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from audit.models import AuditEntry
from boards import services, visibility
from boards.models import PostRevision, SubForum
from core.permissions import can
from tests.factories import grant, make_dm, make_post, make_thread


@pytest.fixture
def scoped_mod(make_user):
    def make(subforum):
        mod = make_user("tenured")
        grant(mod, "moderator", scope_subforum=subforum)
        return mod

    return make


# --- editing -------------------------------------------------------------------------------


def test_author_edits_within_window_only(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author)
    assert can(author, "post.edit", make_post(thread, author, ago=timedelta(minutes=29)))
    assert not can(author, "post.edit", make_post(thread, author, ago=timedelta(minutes=31)))


def test_unlimited_edit_window(make_user, general):
    general.settings["subforum.edit_window_minutes"] = None
    general.save()
    author = make_user("full")
    assert can(author, "post.edit", make_post(make_thread(general, author), author, ago=timedelta(days=400)))


def test_others_cannot_edit(make_user, general):
    author = make_user("full")
    post = make_post(make_thread(general, author), author)
    assert not can(make_user("tenured"), "post.edit", post)


def test_staff_edit_in_scope_at_any_time(make_user, general, serious, scoped_mod):
    author = make_user("full")
    post = make_post(make_thread(general, author), author, ago=timedelta(days=30))
    assert can(scoped_mod(general), "post.edit", post)
    assert not can(scoped_mod(serious), "post.edit", post)
    assert can(make_user("admin"), "post.edit", post)


def test_locked_thread_freezes_authors_not_staff(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author)
    post = make_post(thread, author)
    thread.state = thread.State.LOCKED
    thread.save()
    assert not can(author, "post.edit", post)
    assert can(make_user("moderator"), "post.edit", post)


def test_archived_thread_cannot_be_changed_by_anyone(make_user, general):
    owner_like = make_user("owner")
    author = make_user("full")
    thread = make_thread(general, author)
    post = make_post(thread, author)
    thread.state = thread.State.ARCHIVED
    thread.save()
    for action in ("post.edit", "post.delete"):
        assert not can(author, action, post)
        assert not can(owner_like, action, post)


def test_deleted_and_rejected_posts_cannot_be_edited(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author)
    admin = make_user("admin")
    assert not can(admin, "post.edit", make_post(thread, author, deleted_at=timezone.now()))
    assert not can(admin, "post.edit", make_post(thread, author, rejected_at=timezone.now()))


def test_dm_messages_are_edited_only_by_their_author_within_the_window(make_user):
    author, other = make_user("full"), make_user("full")
    dm = make_dm(author, other)
    assert can(author, "post.edit", make_post(dm, author))
    assert not can(author, "post.edit", make_post(dm, author, ago=timedelta(minutes=31)))
    assert not can(other, "post.edit", make_post(dm, author))
    assert not can(make_user("admin"), "post.edit", make_post(dm, author))


def test_author_may_edit_own_held_post(make_user, general):
    member = make_user("provisional")
    post = services.reply(member, make_thread(general, make_user("full")), "first draft")
    services.edit_post(member, post, "second draft")
    post.refresh_from_db()
    assert post.is_held and post.body_source == "second draft"


def test_revisions_keep_every_version(make_user, general):
    author = make_user("full")
    post = services.reply(author, make_thread(general, author), "one")
    services.edit_post(author, post, "two")
    services.edit_post(author, post, "three")
    post.refresh_from_db()
    assert post.body_source == "three" and post.edited_at is not None
    assert "three" in post.body_html
    assert list(post.revisions.order_by("edited_at", "pk").values_list("body_source", flat=True)) == [
        "one", "two", "three",
    ]


def test_revision_history_is_for_staff(make_user, general):
    author = make_user("full")
    post = services.reply(author, make_thread(general, author), "one")
    assert not can(author, "post.read_revisions", post)
    assert not can(make_user("tenured"), "post.read_revisions", post)
    assert can(make_user("moderator"), "post.read_revisions", post)


def test_staff_edits_of_others_are_audited_author_edits_are_not(make_user, general):
    author, mod = make_user("full"), make_user("moderator")
    post = services.reply(author, make_thread(general, author), "one")
    services.edit_post(author, post, "two")
    assert not AuditEntry.objects.filter(action="post.edit").exists()
    services.edit_post(mod, post, "three")
    assert AuditEntry.objects.get(action="post.edit").actor == mod


def test_edit_refused_without_permission_writes_nothing(make_user, general):
    author = make_user("full")
    post = services.reply(author, make_thread(general, author), "one")
    with pytest.raises(PermissionDenied):
        services.edit_post(make_user("full"), post, "vandalism")
    assert not PostRevision.objects.exists()


# --- deleting ------------------------------------------------------------------------------


def test_author_deletes_own_post_within_window(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author)
    post = services.delete_post(author, make_post(thread, author))
    assert post.deleted_at is not None and post.deleted_by == author
    assert not can(author, "post.delete", make_post(thread, author, ago=timedelta(hours=2)))


def test_staff_delete_needs_reason_and_is_audited(make_user, general):
    author, mod = make_user("full"), make_user("moderator")
    post = make_post(make_thread(general, author), author)
    with pytest.raises(ValidationError):
        services.delete_post(mod, post)
    services.delete_post(mod, post, reason_key="private_information")
    entry = AuditEntry.objects.get(action="post.hide")
    assert entry.actor == mod and entry.payload["reason"] == "Private information"


def test_deleted_post_visible_only_to_admin_and_owner_with_who_and_why(make_user, general):
    author, mod = make_user("full"), make_user("moderator")
    post = services.delete_post(mod, make_post(make_thread(general, author), author), reason_key="spam")
    admin = make_user("admin")
    assert can(admin, "post.read", post) and not can(mod, "post.read", post)
    shown = visibility.visible_posts(admin).get(pk=post.pk)
    assert (shown.deleted_by, shown.delete_reason) == (mod, "Spam")


# --- thread states -------------------------------------------------------------------------


def test_moderators_lock_in_scope(make_user, general, serious, scoped_mod):
    thread = make_thread(general, make_user("full"))
    with pytest.raises(PermissionDenied):
        services.set_locked(scoped_mod(serious), thread, True)
    with pytest.raises(PermissionDenied):
        services.set_locked(make_user("tenured"), thread, True)
    mod = scoped_mod(general)
    assert services.set_locked(mod, thread, True).state == "locked"
    assert services.set_locked(mod, thread, False).state == "open"
    assert AuditEntry.objects.filter(action="thread.lock", actor=mod).count() == 2


def test_pin_and_archive_are_for_admins_and_owners(make_user, general):
    thread = make_thread(general, make_user("full"))
    for fn in (lambda a: services.set_pinned(a, thread, True), lambda a: services.archive_thread(a, thread)):
        with pytest.raises(PermissionDenied):
            fn(make_user("moderator"))
    assert services.set_pinned(make_user("admin"), thread, True).is_pinned
    assert services.archive_thread(make_user("owner"), thread).state == "archived"


def test_archived_thread_state_is_final(make_user, general):
    thread = services.archive_thread(make_user("admin"), make_thread(general, make_user("full")))
    owner_like = make_user("owner")
    for fn in (
        lambda: services.set_locked(owner_like, thread, True),
        lambda: services.set_pinned(owner_like, thread, True),
        lambda: services.archive_thread(owner_like, thread),
    ):
        with pytest.raises(PermissionDenied):
            fn()


# --- visibility ----------------------------------------------------------------------------


@pytest.fixture
def mixed_posts(make_user, general, lobby):
    """Posts in every state, in a member area, the Guest Lobby and a Tenured-only area."""
    tenured_only = SubForum.objects.create(
        name="Tenured", slug="tenured", position=9, settings={"subforum.min_read_role": "tenured"}
    )
    author, provisional = make_user("full"), make_user("provisional")
    posts = {}
    for sf in (general, lobby, tenured_only):
        thread = make_thread(sf, author)
        posts[(sf.slug, "released")] = make_post(thread, author)
        posts[(sf.slug, "held")] = make_post(thread, provisional, is_held=True)
        posts[(sf.slug, "rejected")] = make_post(thread, provisional, rejected_at=timezone.now())
        posts[(sf.slug, "deleted")] = make_post(thread, author, deleted_at=timezone.now())
    return posts, provisional


@pytest.mark.parametrize("role", ["guest", "provisional", "full", "tenured", "moderator", "admin", "owner"])
def test_visible_posts_matches_post_read(make_user, mixed_posts, role):
    posts, _ = mixed_posts
    actor = make_user(role)
    expected = {p.pk for p in posts.values() if can(actor, "post.read", p)}
    assert set(visibility.visible_posts(actor).values_list("pk", flat=True)) == expected


def test_visible_posts_matches_post_read_for_the_held_author(mixed_posts):
    posts, provisional = mixed_posts
    expected = {p.pk for p in posts.values() if can(provisional, "post.read", p)}
    assert set(visibility.visible_posts(provisional).values_list("pk", flat=True)) == expected
    assert posts[("general-discussion", "held")].pk in expected
    assert posts[("general-discussion", "rejected")].pk not in expected


def test_visible_posts_matches_post_read_for_a_scoped_moderator(mixed_posts, general, scoped_mod):
    posts, _ = mixed_posts
    mod = scoped_mod(general)
    expected = {p.pk for p in posts.values() if can(mod, "post.read", p)}
    assert set(visibility.visible_posts(mod).values_list("pk", flat=True)) == expected


def test_thread_with_only_a_held_post_is_hidden_from_others(make_user, lobby):
    guest = make_user("guest")
    thread, _ = services.start_thread(guest, lobby, "Hello", "I'm new")
    assert thread not in visibility.visible_threads(make_user("full"), lobby)
    assert thread in visibility.visible_threads(guest, lobby)
    assert thread in visibility.visible_threads(make_user("moderator"), lobby)


def test_pinned_threads_list_first(make_user, general):
    author = make_user("full")
    old = make_thread(general, author, title="Old", created_at=timezone.now() - timedelta(days=5))
    make_post(old, author)
    old.last_post_at = timezone.now() - timedelta(days=5)
    old.is_pinned = True
    old.save()
    new = make_thread(general, author, title="New")
    make_post(new, author)
    assert list(visibility.visible_threads(author, general)) == [old, new]


def test_rejected_post_is_out_of_the_thread_but_in_author_history(make_user, general):
    member, reader = make_user("provisional"), make_user("full")
    thread = make_thread(general, reader)
    post = services.reply(member, thread, "too hot")
    services.reject_post(make_user("moderator"), post, "tone")
    assert post not in visibility.thread_posts(member, thread)
    assert post in visibility.post_history(member, member)
    assert post not in visibility.post_history(reader, member)


def test_post_history_for_others_shows_only_what_they_could_read(make_user, general, lobby):
    member = make_user("provisional")
    visible = make_post(make_thread(lobby, member), member)
    held = make_post(make_thread(general, member), member, is_held=True)
    deleted = make_post(make_thread(general, member), member, deleted_at=timezone.now(), deleted_by=member)
    guest = make_user("guest")
    assert list(visibility.post_history(guest, member)) == [visible]
    assert set(visibility.post_history(member, member)) == {visible, held}
    assert deleted not in visibility.post_history(member, member)


# --- search --------------------------------------------------------------------------------


def test_search_finds_body_and_title(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author, title="Beekeeping in winter")
    body_match = make_post(thread, author)
    body_match.body_source = "Mulching roses before the frost"
    body_match.save()
    other = make_post(make_thread(general, author, title="Unrelated"), author)
    other.body_source = "Nothing to see"
    other.save()
    assert list(visibility.search_posts(author, "roses")) == [body_match]
    assert body_match in visibility.search_posts(author, "beekeeping")
    assert other not in visibility.search_posts(author, "beekeeping")


def test_search_respects_visibility(make_user, mixed_posts):
    from boards.models import Post

    posts, provisional = mixed_posts
    Post.objects.filter(pk__in=[p.pk for p in posts.values()]).update(body_source="the word marmalade")
    for actor in (make_user("guest"), make_user("full"), provisional, make_user("tenured"), make_user("admin")):
        found = set(visibility.search_posts(actor, "marmalade").values_list("pk", flat=True))
        assert found == set(visibility.visible_posts(actor).values_list("pk", flat=True))


def test_search_uses_web_syntax(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author)
    both = make_post(thread, author)
    both.body_source = "apples and pears"
    both.save()
    apples = make_post(thread, author)
    apples.body_source = "apples only"
    apples.save()
    assert set(visibility.search_posts(author, "apples -pears")) == {apples}
    assert set(visibility.search_posts(author, '"apples and pears"')) == {both}


def test_search_refused_for_closed_accounts(make_user, general):
    from accounts.models import User

    author = make_user("full")
    make_post(make_thread(general, author), author)
    banned = make_user("full", status=User.Status.BANNED)
    assert not visibility.search_posts(banned, "text").exists()
