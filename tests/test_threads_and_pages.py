"""Rules 25 to 28: thread titles, moves, endings (Graveyard, Classics, archive), redaction, purge,
profiles; held posts and thread activity; and the page sizes."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from audit.models import AuditEntry
from boards import services, visibility
from boards.models import SubForum, Thread, ThreadTitleRevision
from core.permissions import MoveRequest, can
from tests.factories import enrol_totp, grant, make_dm, make_post, make_thread


@pytest.fixture
def graveyard(seeded):
    return SubForum.objects.get(kind="graveyard")


@pytest.fixture
def classics(seeded):
    return SubForum.objects.get(kind="classics")


@pytest.fixture
def scoped_mod(make_user):
    def make(*subforums):
        mod = make_user("tenured")
        for sf in subforums:
            grant(mod, "moderator", scope_subforum=sf)
        return mod

    return make


def _thread(make_user, subforum, author=None):
    author = author or make_user("full")
    thread, _ = services.start_thread(author, subforum, "A thread", "Opening post")
    return thread


# --- the Graveyard -------------------------------------------------------------------------


def test_sending_to_the_graveyard(make_user, general, graveyard, scoped_mod):
    thread = _thread(make_user, general)
    mod = scoped_mod(general)
    with pytest.raises(ValidationError):
        services.send_to_graveyard(mod, thread, "  ")
    thread = services.send_to_graveyard(mod, thread, "Personal attacks")
    assert thread.subforum == graveyard and thread.origin_subforum == general
    assert thread.state == "archived" and thread.ended_by == mod and thread.end_reason == "Personal attacks"
    assert AuditEntry.objects.filter(action="thread.graveyard", actor=mod).exists()


@pytest.mark.parametrize("role, allowed", [("full", False), ("tenured", False), ("moderator", True),
                                           ("admin", True), ("owner", True)])
def test_who_sends_to_the_graveyard(make_user, general, role, allowed):
    thread = _thread(make_user, general)
    assert bool(can(make_user(role), "thread.graveyard", thread)) is allowed


def test_moderator_out_of_scope_cannot(make_user, general, serious, scoped_mod):
    assert not can(scoped_mod(serious), "thread.graveyard", _thread(make_user, general))


def test_graveyard_is_readable_from_provisional_up_and_read_only(make_user, general, graveyard):
    thread = services.send_to_graveyard(make_user("admin"), _thread(make_user, general), "spam")
    assert not can(make_user("guest"), "thread.read", thread)
    assert can(make_user("provisional"), "thread.read", thread)
    assert not can(make_user("owner"), "thread.reply", thread)
    assert not can(make_user("owner"), "subforum.start_thread", graveyard)


def test_audience_warning(make_user, general, lobby, graveyard):
    tenured_only = SubForum.objects.create(name="Inner", slug="inner", settings={"subforum.min_read_role": "tenured"})
    assert services.widens_audience(tenured_only, graveyard)
    assert not services.widens_audience(general, graveyard)
    assert not services.widens_audience(lobby, graveyard)
    thread = services.send_to_graveyard(make_user("admin"), _thread(make_user, tenured_only, make_user("tenured")), "x")
    assert AuditEntry.objects.get(action="thread.graveyard").payload["widened_audience"] is True
    assert thread.subforum == graveyard


def test_audience_warning_on_the_confirmation_page(client, make_user, graveyard):
    admin = make_user("admin")
    enrol_totp(admin)
    client.force_login(admin)
    inner = SubForum.objects.create(name="Inner", slug="inner", settings={"subforum.min_read_role": "tenured"})
    thread = _thread(make_user, inner, make_user("tenured"))
    assert b"more people than can read it now" in client.get(f"/t/{thread.pk}/end/graveyard/").content


def test_dm_cannot_be_ended(make_user):
    dm = make_dm(make_user("full"), make_user("full"))
    for action in ("thread.graveyard", "thread.classics", "thread.archive"):
        assert not can(make_user("owner"), action, dm)


# --- the Classics and archiving ------------------------------------------------------------


@pytest.mark.parametrize("role, allowed", [("moderator", False), ("admin", True), ("owner", True)])
def test_classics_and_archive_are_for_admins_and_owners(make_user, general, role, allowed):
    thread = _thread(make_user, general)
    assert bool(can(make_user(role), "thread.classics", thread)) is allowed
    assert bool(can(make_user(role), "thread.archive", thread)) is allowed


def test_classics_honour_on_the_profile(client, make_user, general, classics):
    author = make_user("full")
    thread = services.send_to_classics(make_user("admin"), _thread(make_user, general, author))
    assert thread.subforum == classics and thread.origin_subforum == general
    viewer = make_user("provisional")
    enrol_totp(viewer)
    client.force_login(viewer)
    page = client.get(f"/members/{author.slug}/").content.decode()
    assert "Thread Classics" in page and f"/t/{thread.pk}/" in page


# --- restoring -----------------------------------------------------------------------------


@pytest.mark.parametrize("ending", ["graveyard", "classics", "archive"])
def test_only_owners_restore(make_user, general, ending):
    thread = _thread(make_user, general)
    admin = make_user("admin")
    end = {"graveyard": lambda t: services.send_to_graveyard(admin, t, "x"),
           "classics": lambda t: services.send_to_classics(admin, t),
           "archive": lambda t: services.archive_thread(admin, t)}[ending]
    thread = end(thread)
    with pytest.raises(PermissionDenied):
        services.restore_thread(admin, thread)
    thread = services.restore_thread(make_user("owner"), thread)
    assert thread.subforum == general and thread.state == "open" and thread.origin_subforum is None
    assert AuditEntry.objects.filter(action="thread.restore").exists()


def test_nothing_else_changes_an_ended_thread(make_user, general):
    thread = services.archive_thread(make_user("admin"), _thread(make_user, general))
    owner = make_user("owner")
    for action in ("thread.lock", "thread.pin", "thread.graveyard", "thread.classics", "thread.archive"):
        assert not can(owner, action, thread)
    assert not can(owner, "thread.move", MoveRequest(thread, SubForum.objects.get(slug="seminars")))


# --- redaction and purge -------------------------------------------------------------------


def test_admin_redacts_in_the_graveyard(make_user, general):
    author = make_user("full")
    thread = _thread(make_user, general, author)
    post = thread.posts.get()
    thread = services.send_to_graveyard(make_user("moderator"), thread, "doxxing")
    post.refresh_from_db()
    post.thread = thread
    for role in ("moderator", "full"):
        assert not can(make_user(role), "post.edit", post)
    assert not can(author, "post.edit", post)
    admin = make_user("admin")
    services.edit_post(admin, post, "[removed]")
    services.edit_title(admin, thread, "Removed thread")
    assert post.revisions.filter(is_redaction=True).exists()
    assert AuditEntry.objects.filter(action="post.redact", actor=admin).exists()
    assert not can(admin, "post.delete", post)


def test_redacted_note_shows_in_the_thread(client, make_user, general):
    author = make_user("full")
    thread = _thread(make_user, general, author)
    services.send_to_graveyard(make_user("admin"), thread, "x")
    services.edit_post(make_user("admin"), thread.posts.get(), "[removed]")
    reader = make_user("provisional")
    enrol_totp(reader)
    client.force_login(reader)
    assert b"redacted by staff" in client.get(f"/t/{thread.pk}/").content


def test_owner_purges_earlier_revisions_of_graveyard_posts_only(make_user, general):
    author = make_user("full")
    thread = _thread(make_user, general, author)
    post = thread.posts.get()
    services.edit_post(author, post, "second version")
    assert not can(make_user("owner"), "post.purge_revisions", post)
    post.thread = services.send_to_graveyard(make_user("admin"), thread, "x")
    services.edit_post(make_user("admin"), post, "[removed]")
    with pytest.raises(PermissionDenied):
        services.purge_revisions(make_user("admin"), post)
    assert services.purge_revisions(make_user("owner"), post) == 2
    bodies = list(post.revisions.order_by("edited_at", "pk").values_list("body_source", flat=True))
    assert bodies == ["", "", "[removed]"]
    assert post.revisions.filter(purged_by__isnull=False).count() == 2


# --- moves and titles ----------------------------------------------------------------------


def test_moderators_move_only_between_their_sub_forums(make_user, general, serious, seminars, scoped_mod):
    thread = _thread(make_user, general)
    mod = scoped_mod(general, serious)
    assert can(mod, "thread.move", MoveRequest(thread, serious))
    assert not can(mod, "thread.move", MoveRequest(thread, seminars))
    assert can(make_user("admin"), "thread.move", MoveRequest(thread, seminars))
    services.move_thread(mod, thread, serious)
    thread.refresh_from_db()
    assert thread.subforum == serious


def test_moving_is_not_how_threads_reach_the_ending_areas(make_user, general, graveyard, classics):
    thread = _thread(make_user, general)
    for area in (graveyard, classics):
        assert not can(make_user("owner"), "thread.move", MoveRequest(thread, area))


def test_starter_edits_the_title_within_the_window_staff_any_time(make_user, general):
    author = make_user("full")
    thread = _thread(make_user, general, author)
    services.edit_title(author, thread, "Better title")
    assert list(ThreadTitleRevision.objects.filter(thread=thread).values_list("title", flat=True)) == [
        "A thread", "Better title",
    ]
    thread.posts.update(created_at=timezone.now() - timedelta(hours=1))
    assert not can(author, "thread.edit_title", thread)
    assert not can(make_user("full"), "thread.edit_title", thread)
    assert can(make_user("moderator"), "thread.edit_title", thread)
    assert not can(author, "thread.read_title_revisions", thread)
    assert can(make_user("moderator"), "thread.read_title_revisions", thread)


def test_starter_cannot_retitle_a_locked_thread(make_user, general):
    author = make_user("full")
    thread = services.set_locked(make_user("moderator"), _thread(make_user, general, author), True)
    assert not can(author, "thread.edit_title", thread)


def test_staff_edit_is_marked(client, make_user, general):
    author = make_user("full")
    thread = _thread(make_user, general, author)
    services.edit_post(make_user("moderator"), thread.posts.get(), "tidied")
    enrol_totp(author)
    client.force_login(author)
    assert b"edited by staff" in client.get(f"/t/{thread.pk}/").content


# --- held posts and activity ---------------------------------------------------------------


def test_held_post_is_not_activity_until_released(make_user, general):
    author = make_user("full")
    thread = _thread(make_user, general, author)
    Thread.objects.filter(pk=thread.pk).update(last_post_at=timezone.now() - timedelta(days=1))
    thread.refresh_from_db()
    before, count = thread.last_post_at, thread.post_count
    held = services.reply(make_user("provisional"), thread, "pending")
    thread.refresh_from_db()
    assert (thread.last_post_at, thread.post_count) == (before, count)
    services.release_post(make_user("moderator"), held)
    thread.refresh_from_db()
    assert thread.last_post_at > before and thread.post_count == count + 1


# --- profiles ------------------------------------------------------------------------------


def test_post_count_only_for_the_member(client, make_user, general):
    member = make_user("full")
    _thread(make_user, general, member)
    enrol_totp(member)
    client.force_login(member)
    assert b"Only you see this: 1 post" in client.get(f"/members/{member.slug}/").content
    other = make_user("full")
    enrol_totp(other)
    client.force_login(other)
    assert b"Only you see this" not in client.get(f"/members/{member.slug}/").content


def test_disciplinary_record_for_provisional_and_above(client, make_user):
    from moderation import services as moderation

    member = make_user("full")
    moderation.initiate_action(make_user("admin"), member, "warning", internal_reason="x", public_summary="Rude")
    guest, provisional = make_user("guest"), make_user("provisional")
    for user in (guest, provisional):
        enrol_totp(user)
    client.force_login(guest)
    assert b"Disciplinary record" not in client.get(f"/members/{member.slug}/").content
    client.force_login(provisional)
    assert b"Rude" in client.get(f"/members/{member.slug}/").content


def test_invited_accounts_have_no_profile(client, make_user):
    invited = make_user(None, status="invited")
    viewer = make_user("full")
    enrol_totp(viewer)
    client.force_login(viewer)
    assert client.get(f"/members/{invited.slug}/").status_code == 404
    response = client.get(f"/members/autocomplete/?q={invited.slug[:6]}")
    assert invited.slug not in response.content.decode()


# --- page sizes ----------------------------------------------------------------------------


@pytest.fixture
def reader(client, make_user):
    user = make_user("full")
    enrol_totp(user)
    client.force_login(user)
    return user


def test_thread_pages_hold_20_posts(client, reader, general):
    thread = make_thread(general, reader)
    for _ in range(25):
        make_post(thread, reader)
    first = client.get(f"/t/{thread.pk}/")
    assert len(first.context["entries"]) == 20
    assert len(client.get(f"/t/{thread.pk}/?page=2").context["entries"]) == 5
    assert len(client.get(f"/t/{thread.pk}/?page=all").context["entries"]) == 20


def test_sub_forum_pages_hold_30_threads(client, reader, general):
    for _ in range(35):
        make_post(make_thread(general, reader), reader)
    assert len(client.get("/f/general-discussion/").context["page"].object_list) == 30


def test_search_and_profile_pages_hold_20(client, reader, general):
    thread = make_thread(general, reader, title="Gardening")
    for _ in range(25):
        post = make_post(thread, reader)
        post.body_source = "tomatoes"
        post.save()
    assert len(client.get("/search/?q=tomatoes").context["page"].object_list) == 20
    assert len(client.get(f"/members/{reader.slug}/").context["page"].object_list) == 20


def test_index_counts_only_what_the_reader_can_see(client, reader, general):
    thread = make_thread(general, reader)
    make_post(thread, reader)
    make_post(thread, reader, is_held=True)
    rows = {row["subforum"].slug: row for row in client.get("/").context["regular"]}
    assert rows["general-discussion"]["posts"] == 1 and rows["general-discussion"]["threads"] == 1
    assert visibility.visible_threads(reader, general).count() == 1
