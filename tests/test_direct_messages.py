"""Direct messages: every row of the Direct messages table in docs/DESIGN.md, rules 30 to 32."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts.models import Block, User
from audit.models import AuditEntry
from boards import dm, messages, services
from boards.models import Post, ThreadParticipant
from core.models import Notification
from core.permissions import can
from core.services import set_site_setting
from moderation.models import ModerationAction
from tests.factories import enrol_totp, grant, make_thread, sponsor


def _start(actor, *others, body="hello"):
    return messages.start(actor, list(others), "", body)


def _in_force(user, kind, admin):
    return ModerationAction.objects.create(
        target_user=user, kind=kind, initiated_by=admin, status="active", starts_at=timezone.now(),
        internal_reason="test",
    )


# --- who may message whom --------------------------------------------------------------------


@pytest.mark.parametrize("a, b", [("full", "full"), ("full", "tenured"), ("tenured", "moderator"),
                                  ("full", "admin"), ("owner", "full")])
def test_full_and_above_message_each_other(make_user, a, b):
    assert dm.may_message(make_user(a), make_user(b))


@pytest.mark.parametrize("newcomer", ["guest", "provisional"])
def test_newcomers_only_with_their_sponsor_and_staff(make_user, lobby, newcomer):
    member, own_sponsor = make_user(newcomer), make_user("full")
    sponsor(own_sponsor, member)
    assert dm.may_message(member, own_sponsor) and dm.may_message(own_sponsor, member)
    assert not dm.may_message(member, make_user("full"))
    assert not dm.may_message(make_user("tenured"), member)
    assert not dm.may_message(member, make_user(newcomer))
    assert dm.may_message(member, make_user("admin")) and dm.may_message(make_user("owner"), member)


def test_staff_means_moderators_of_a_sub_forum_the_member_can_read(make_user, lobby, serious):
    guest = make_user("guest")
    lobby_mod, serious_mod = make_user("tenured"), make_user("tenured")
    grant(lobby_mod, "moderator", scope_subforum=lobby)
    grant(serious_mod, "moderator", scope_subforum=serious)
    assert dm.may_message(guest, lobby_mod)
    assert not dm.may_message(guest, serious_mod)
    assert dm.may_message(guest, make_user("moderator"))  # a global Moderator


@pytest.mark.parametrize("restriction", ["read_only_status", "suspended_status", "probation", "suspension"])
def test_restricted_members_only_with_sponsor_and_staff(make_user, restriction):
    admin = make_user("admin")
    status = {"read_only_status": User.Status.READ_ONLY, "suspended_status": User.Status.SUSPENDED}.get(restriction)
    member = make_user("full", status=status) if status else make_user("full")
    if not status:
        _in_force(member, {"probation": "read_only", "suspension": "suspension"}[restriction], admin)
    own_sponsor = make_user("tenured")
    sponsor(own_sponsor, member)
    assert dm.may_message(member, own_sponsor) and dm.may_message(member, admin)
    assert not dm.may_message(member, make_user("full"))


def test_banned_member_only_with_admins_and_owners(make_user):
    admin = make_user("admin")
    banned = make_user("full")
    own_sponsor = make_user("tenured")
    sponsor(own_sponsor, banned)
    _in_force(banned, "ban", admin)
    assert dm.may_message(banned, admin) and dm.may_message(banned, make_user("owner"))
    assert not dm.may_message(banned, own_sponsor)
    assert not dm.may_message(banned, make_user("moderator"))


def test_banned_member_can_appeal_through_the_pages(client, make_user):
    admin, banned = make_user("admin"), make_user("full")
    _in_force(banned, "ban", admin)
    enrol_totp(banned)
    client.force_login(banned)
    response = client.post("/messages/new/", {"to": admin.slug, "subject": "Appeal", "body": "Please reconsider"})
    assert response.status_code == 302
    assert Post.objects.filter(author=banned, thread__kind="dm").exists()


def test_existing_conversation_stays_readable_but_closed(make_user):
    admin = make_user("admin")
    a, b = make_user("full"), make_user("full")
    thread, _ = _start(a, b)
    _in_force(a, "read_only", admin)
    assert can(a, "thread.read", thread)
    assert not can(a, "thread.reply", thread)
    assert "can no longer message" in can(a, "thread.reply", thread).reason


# --- groups ----------------------------------------------------------------------------------


def test_group_cap(make_user, owner):
    set_site_setting(owner, "dm.max_participants", 3)
    starter = make_user("full")
    with pytest.raises(PermissionDenied):
        _start(starter, make_user("full"), make_user("full"), make_user("full"))
    thread, _ = _start(starter, make_user("full"), make_user("full"))
    with pytest.raises(PermissionDenied):
        messages.add(starter, thread, make_user("full"))


def test_every_pair_must_be_allowed(make_user):
    starter, newcomer = make_user("full"), make_user("guest")
    sponsor(starter, newcomer)
    with pytest.raises(PermissionDenied):
        _start(starter, newcomer, make_user("full"))  # the third Full member may not message the Guest
    thread, _ = _start(starter, newcomer)
    with pytest.raises(PermissionDenied):
        messages.add(starter, thread, make_user("full"))


def test_anyone_in_it_can_add_and_the_record_shows_who(make_user):
    a, b, c = make_user("full"), make_user("full"), make_user("full")
    thread, _ = _start(a, b)
    messages.add(b, thread, c)
    assert ThreadParticipant.objects.get(thread=thread, user=c).added_by == b


def test_newcomer_reads_only_from_joining(make_user):
    a, b, c = make_user("full"), make_user("full"), make_user("full")
    thread, first = _start(a, b, body="before")
    Post.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(minutes=5))
    messages.add(a, thread, c)
    after = services.reply(a, thread, "after")
    assert list(messages.messages(c, thread)) == [after]
    first.refresh_from_db()
    assert not can(c, "post.read", first)


def test_leaving_keeps_read_access_to_the_past_and_rejoining_skips_the_gap(make_user):
    a, b, c = make_user("full"), make_user("full"), make_user("full")
    thread, first = _start(a, b, c, body="one")
    messages.leave(c, thread)
    assert not can(c, "thread.reply", thread)
    later = services.reply(a, thread, "two")
    assert first in messages.messages(c, thread) and later not in messages.messages(c, thread)
    messages.add(a, thread, c)
    third = services.reply(a, thread, "three")
    seen = set(messages.messages(c, thread))
    assert first in seen and third in seen and later not in seen


def test_members_cannot_remove_each_other_admins_can(make_user):
    a, b, c = make_user("full"), make_user("full"), make_user("full")
    thread, _ = _start(a, b, c)
    with pytest.raises(PermissionDenied):
        messages.remove(a, thread, c)
    with pytest.raises(PermissionDenied):
        messages.remove(make_user("moderator"), thread, c)
    messages.remove(make_user("admin"), thread, c)
    assert not can(c, "thread.reply", thread)
    assert AuditEntry.objects.filter(action="dm.remove").exists()


# --- content and counting --------------------------------------------------------------------


def test_messages_are_never_held_and_count_toward_nothing(make_user, serious):
    from sponsorship.eligibility import full_promotion_eligible

    member = make_user("provisional", granted_at=timezone.now() - timedelta(days=100))
    own_sponsor = make_user("full")
    sponsor(own_sponsor, member)
    thread, first = _start(member, own_sponsor)
    for _ in range(30):
        assert not services.reply(member, thread, "chat").is_held
    assert not full_promotion_eligible(member)
    assert can(member, "thread.reply", make_thread(serious, own_sponsor))
    assert services.reply(member, make_thread(serious, own_sponsor), "first forum post").is_held


def test_new_conversation_cap(make_user, owner):
    set_site_setting(owner, "dm.max_new_conversations_per_day", 2)
    starter = make_user("full")
    _start(starter, make_user("full"))
    _start(starter, make_user("full"))
    with pytest.raises(PermissionDenied):
        _start(starter, make_user("full"))


def test_outside_links_follow_full_and_above(make_user):
    newcomer, own_sponsor = make_user("provisional"), make_user("full")
    sponsor(own_sponsor, newcomer)
    thread, from_newcomer = _start(newcomer, own_sponsor, body="see https://example.org")
    from_sponsor = services.reply(own_sponsor, thread, "and https://example.net")
    assert "<a" not in from_newcomer.body_html
    assert 'href="https://example.net" rel="nofollow noopener noreferrer"' in from_sponsor.body_html


def test_deleted_message_stays_visible_to_admins(make_user):
    a, b = make_user("full"), make_user("full")
    thread, post = _start(a, b)
    services.delete_post(a, post)
    assert post not in messages.messages(b, thread)
    assert post in messages.messages(make_user("admin"), thread)


# --- blocking --------------------------------------------------------------------------------


def test_blocking(make_user):
    a, b = make_user("full"), make_user("full")
    thread, _ = _start(a, b)
    messages.block(a, b)
    assert not can(b, "thread.reply", thread)          # one-to-one stops taking b's messages
    assert can(a, "thread.reply", thread)
    with pytest.raises(PermissionDenied):
        _start(b, a)
    group, _ = _start(b, make_user("full"))
    with pytest.raises(PermissionDenied):
        messages.add(b, group, a)  # nor add the blocker to a group


def test_blocked_member_cannot_notify_with_a_mention(make_user, general):
    a, b = make_user("full"), make_user("full")
    messages.block(a, b)
    services.reply(b, make_thread(general, b), f"hey @{a.slug}")
    assert not Notification.objects.filter(recipient=a, kind="mention").exists()


def test_groups_are_unaffected_by_blocks_made_later(make_user):
    a, b, c = make_user("full"), make_user("full"), make_user("full")
    thread, _ = _start(a, b, c)
    messages.block(a, b)
    assert can(b, "thread.reply", thread)
    assert can(a, "dm.leave", thread)


@pytest.mark.parametrize("staff_role", ["moderator", "admin", "owner"])
def test_staff_cannot_be_blocked(make_user, staff_role):
    member, staff = make_user("full"), make_user(staff_role)
    with pytest.raises(PermissionDenied):
        messages.block(member, staff)
    Block.objects.create(blocker=member, blocked=staff)  # even a stray row is ignored
    assert not dm.has_blocked(member, staff)


def test_blocked_member_is_not_told(client, make_user):
    a, b = make_user("full"), make_user("full")
    messages.block(a, b)
    assert not Notification.objects.filter(recipient=b).exists()
    enrol_totp(b)
    client.force_login(b)
    assert b"blocked" not in client.get(f"/members/{a.slug}/").content.lower()


# --- read position, notice, notifications, audit ---------------------------------------------


def test_unread_counts_are_private(make_user):
    a, b = make_user("full"), make_user("full")
    thread, _ = _start(a, b)
    assert messages.unread_count(b) == 1 and messages.unread_count(a) == 0
    messages.mark_read(b, thread)
    assert messages.unread_count(b) == 0


def test_one_notification_per_conversation_until_read(make_user):
    a, b = make_user("full"), make_user("full")
    thread, _ = _start(a, b)
    services.reply(a, thread, "again")
    assert Notification.objects.filter(recipient=b, kind="dm").count() == 1
    messages.mark_read(b, thread)
    services.reply(a, thread, "and again")
    assert Notification.objects.filter(recipient=b, kind="dm", read_at__isnull=True).count() == 1


def test_notice_on_every_conversation_and_compose_box(client, make_user):
    a, b = make_user("full"), make_user("full")
    thread, _ = _start(a, b)
    enrol_totp(a)
    client.force_login(a)
    from django.utils.html import escape

    page = client.get(f"/messages/{thread.pk}/").content.decode()
    assert escape(messages.NOTICE) in page and escape(messages.SHORT_NOTICE) in page
    assert escape(messages.SHORT_NOTICE) in client.get("/messages/new/").content.decode()


def test_admin_reads_are_audited_participant_reads_are_not(client, make_user):
    a, b, admin = make_user("full"), make_user("full"), make_user("admin")
    thread, _ = _start(a, b)
    for user in (a, admin):
        enrol_totp(user)
    client.force_login(a)
    client.get(f"/messages/{thread.pk}/")
    assert not AuditEntry.objects.filter(action="dm.read").exists()
    client.force_login(admin)
    client.get(f"/messages/{thread.pk}/")
    entry = AuditEntry.objects.get(action="dm.read")
    assert entry.actor == admin and entry.target_id == str(thread.pk)


def test_outsiders_cannot_open_a_conversation(client, make_user):
    a, b, outsider = make_user("full"), make_user("full"), make_user("full")
    thread, post = _start(a, b)
    enrol_totp(outsider)
    client.force_login(outsider)
    assert client.get(f"/messages/{thread.pk}/").status_code == 404
    assert client.get(f"/p/{post.pk}/").status_code == 404
