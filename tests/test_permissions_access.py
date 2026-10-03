"""Role rank against sub-forum minimums, account status, and deny-by-default."""

import pytest
from django.contrib.auth.models import AnonymousUser

from accounts.models import User
from core.permissions import can
from tests.factories import grant, make_post, make_thread


def test_unknown_action_is_denied(make_user):
    owner_like = make_user("owner")
    assert not can(owner_like, "anything.at.all")


def test_anonymous_is_denied_everything(general):
    assert not can(AnonymousUser(), "subforum.read", general)
    assert not can(None, "subforum.read", general)


@pytest.mark.parametrize(
    "role, lobby_ok, general_ok",
    [
        ("guest", True, False),
        ("provisional", True, True),
        ("full", True, True),
        ("tenured", True, True),
        ("moderator", True, True),
        ("admin", True, True),
        ("owner", True, True),
    ],
)
def test_read_by_role(make_user, lobby, general, role, lobby_ok, general_ok):
    user = make_user(role)
    assert bool(can(user, "subforum.read", lobby)) is lobby_ok
    assert bool(can(user, "subforum.read", general)) is general_ok


def test_member_with_no_role_reads_nothing(make_user, lobby):
    assert not can(make_user(None), "subforum.read", lobby)


def test_min_read_role_above_member(make_user, general):
    general.settings["subforum.min_read_role"] = "tenured"
    general.save()
    assert not can(make_user("full"), "subforum.read", general)
    assert can(make_user("tenured"), "subforum.read", general)
    assert can(make_user("admin"), "subforum.read", general)


def test_admin_reads_regardless_of_minimum(make_user, general):
    general.settings["subforum.min_read_role"] = "owner"
    general.save()
    assert can(make_user("admin"), "subforum.read", general)


def test_guest_may_start_threads_and_reply_in_lobby_only(make_user, lobby, general):
    guest = make_user("guest")
    assert can(guest, "subforum.start_thread", lobby)
    assert can(guest, "thread.reply", make_thread(lobby, make_user("full")))
    assert not can(guest, "subforum.start_thread", general)


def test_separate_thread_and_reply_minimums(make_user, general):
    general.settings["subforum.min_thread_role"] = "tenured"
    general.save()
    full = make_user("full")
    assert not can(full, "subforum.start_thread", general)
    assert can(full, "thread.reply", make_thread(general, make_user("tenured")))


def test_nested_subforum_needs_parent_access(make_user, general):
    from boards.models import SubForum

    child = SubForum.objects.create(parent=general, name="Child", slug="child", settings={
        "subforum.min_read_role": "guest",
    })
    assert not can(make_user("guest"), "subforum.read", child)
    assert can(make_user("provisional"), "subforum.read", child)


@pytest.mark.parametrize("status", [User.Status.READ_ONLY, User.Status.SUSPENDED])
def test_restricted_status_reads_but_cannot_post(make_user, general, status):
    user = make_user("full", status=status)
    assert can(user, "subforum.read", general)
    assert not can(user, "subforum.start_thread", general)
    assert not can(user, "thread.reply", make_thread(general, make_user("full")))


@pytest.mark.parametrize("status", [User.Status.BANNED, User.Status.REMOVED, User.Status.INVITED])
def test_closed_status_reads_nothing(make_user, lobby, status):
    assert not can(make_user("full", status=status), "subforum.read", lobby)


def test_locked_thread_takes_replies_from_leadership_and_its_moderators(make_user, general, serious):
    full = make_user("full")
    thread = make_thread(general, full)
    thread.state = thread.State.LOCKED
    thread.save()
    assert not can(full, "thread.reply", thread)
    assert can(make_user("admin"), "thread.reply", thread)
    assert can(make_user("owner"), "thread.reply", thread)
    assert can(make_user("moderator"), "thread.reply", thread)
    in_scope, out_of_scope = make_user("tenured"), make_user("tenured")
    grant(in_scope, "moderator", scope_subforum=general)
    grant(out_of_scope, "moderator", scope_subforum=serious)
    assert can(in_scope, "thread.reply", thread)
    assert not can(out_of_scope, "thread.reply", thread)


def test_archived_is_read_only_for_everyone(make_user, general):
    full = make_user("full")
    thread = make_thread(general, full)
    thread.state = thread.State.ARCHIVED
    thread.save()
    assert not can(make_user("owner"), "thread.reply", thread)
    general.is_archived = True
    general.save()
    assert can(full, "subforum.read", general)
    assert not can(full, "subforum.start_thread", general)
    assert not can(make_user("admin"), "subforum.start_thread", general)


def test_moderator_rank_counts_only_where_they_moderate(make_user, general, serious):
    for subforum in (general, serious):
        subforum.settings["subforum.min_thread_role"] = "moderator"
        subforum.save()
    member = make_user("tenured")
    grant(member, "moderator", scope_subforum=general)
    assert can(member, "subforum.start_thread", general)
    assert not can(member, "subforum.start_thread", serious)
    assert can(make_user("moderator"), "subforum.start_thread", serious)


def test_deleted_post_visible_only_to_admin_and_owner(make_user, general):
    from django.utils import timezone

    author = make_user("full")
    post = make_post(make_thread(general, author), author, deleted_at=timezone.now())
    assert not can(author, "post.read", post)
    assert not can(make_user("moderator"), "post.read", post)
    assert can(make_user("admin"), "post.read", post)
    assert can(make_user("owner"), "post.read", post)


def test_only_owner_writes_site_settings_and_destroys_data(make_user):
    assert can(make_user("owner"), "site_setting.write", "auth.require_totp")
    assert not can(make_user("admin"), "site_setting.write", "auth.require_totp")
    assert can(make_user("owner"), "data.destroy")
    assert not can(make_user("admin"), "data.destroy")


def test_identity_data_is_admin_only(make_user):
    member = make_user("provisional")
    assert can(make_user("admin"), "identity.read", member)
    assert not can(make_user("moderator"), "identity.read", member)
