"""Build step 4 part 3: declining, withdrawing, scoped actions, Probation, public summaries,
expiry and lifting bans (rule 36)."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts import roles
from accounts.models import User
from audit.models import AuditEntry
from boards import dm, services
from core.models import Notification
from core.permissions import can
from moderation import services as moderation
from moderation.models import ModerationAction
from tests.factories import enrol_totp, grant, make_thread

Status = ModerationAction.Status


@pytest.fixture
def scoped_mod(make_user):
    def make(*subforums):
        mod = make_user("tenured")
        for sf in subforums:
            grant(mod, "moderator", scope_subforum=sf)
        return mod

    return make


def _record(member):
    return ModerationAction.objects.filter(target_user=member, is_public=True, status__in=ModerationAction.RECORD_STATUSES)


# --- decline and withdraw ------------------------------------------------------------------


def test_decline_tells_the_initiator_and_stays_off_the_record(make_user):
    mod, other, target = make_user("moderator"), make_user("moderator"), make_user("full")
    action = moderation.initiate_action(mod, target, "warning", internal_reason="tone", public_summary="Tone")
    with pytest.raises(ValidationError):
        moderation.decline_action(other, action, "")
    with pytest.raises(PermissionDenied):
        moderation.decline_action(mod, action, "my own")
    moderation.decline_action(other, action, "Not warranted")
    action.refresh_from_db()
    assert (action.status, action.declined_by, action.decline_reason) == (Status.DECLINED, other, "Not warranted")
    assert Notification.objects.get(recipient=mod, kind="moderation.declined").payload["reason"] == "Not warranted"
    assert not _record(target).exists()
    with pytest.raises(PermissionDenied):
        moderation.approve_action(make_user("admin"), action)


def test_initiator_withdraws_before_approval_only(make_user):
    mod, target = make_user("moderator"), make_user("full")
    action = moderation.initiate_action(mod, target, "warning", internal_reason="x")
    with pytest.raises(PermissionDenied):
        moderation.withdraw_action(make_user("moderator"), action)
    moderation.withdraw_action(mod, action)
    action.refresh_from_db()
    assert action.status == Status.WITHDRAWN and not _record(target).exists()
    approved = moderation.initiate_action(mod, target, "warning", internal_reason="y")
    moderation.approve_action(make_user("moderator"), approved)
    with pytest.raises(PermissionDenied):
        moderation.withdraw_action(mod, approved)


# --- scope ---------------------------------------------------------------------------------


def test_moderator_suspension_applies_only_in_their_sub_forum(make_user, general, serious, scoped_mod):
    mod, approver = scoped_mod(general), scoped_mod(general)
    target = make_user("full")
    action = moderation.initiate_action(
        mod, target, "suspension", internal_reason="flaming", scope_subforum=general,
        ends_at=timezone.now() + timedelta(days=3),
    )
    moderation.approve_action(approver, action)
    assert not can(target, "subforum.start_thread", general)
    assert not can(target, "thread.reply", make_thread(general, make_user("full")))
    assert can(target, "thread.reply", make_thread(serious, make_user("full")))
    assert dm.standing(target) == dm.NORMAL  # a limited suspension does not restrict DMs


def test_moderator_cannot_limit_to_a_sub_forum_they_do_not_moderate(make_user, general, serious, scoped_mod):
    with pytest.raises(PermissionDenied):
        moderation.initiate_action(scoped_mod(general), make_user("full"), "suspension", internal_reason="x",
                                   scope_subforum=serious)


def test_only_suspensions_and_holds_take_a_scope(make_user, general):
    with pytest.raises(PermissionDenied):
        moderation.initiate_action(make_user("admin"), make_user("full"), "warning", internal_reason="x",
                                   scope_subforum=general)


@pytest.mark.parametrize("kind", ["suspension", "hold", "probation", "ban"])
def test_sitewide_actions_and_probation_refuse_a_moderator_only_pair(make_user, kind):
    mod, other_mod, target = make_user("moderator"), make_user("moderator"), make_user("full")
    action = moderation.initiate_action(mod, target, kind, internal_reason="x")
    assert action.status == Status.PENDING
    assert not can(other_mod, "moderation.approve", action)
    moderation.approve_action(make_user("admin"), action)
    action.refresh_from_db()
    assert action.status == Status.ACTIVE


def test_limited_hold_holds_posts_only_there(make_user, general, serious):
    target = make_user("full")
    moderation.initiate_action(make_user("admin"), target, "hold", internal_reason="x", scope_subforum=general)
    assert services.reply(target, make_thread(general, make_user("full")), "held").is_held
    assert not services.reply(target, make_thread(serious, make_user("full")), "free").is_held


def test_record_shows_where_a_limited_action_applies(client, make_user, general):
    target, viewer = make_user("full"), make_user("full")
    moderation.initiate_action(make_user("admin"), target, "suspension", internal_reason="x",
                               public_summary="Flaming", scope_subforum=general)
    enrol_totp(viewer)
    client.force_login(viewer)
    assert b"Suspension in General Discussion" in client.get(f"/members/{target.slug}/").content


# --- Probation -----------------------------------------------------------------------------


def test_probation_is_sitewide_read_only(make_user, general):
    target = make_user("full")
    moderation.initiate_action(make_user("admin"), target, "probation", internal_reason="x")
    assert can(target, "subforum.read", general)
    assert not can(target, "thread.reply", make_thread(general, make_user("full")))
    assert dm.standing(target) == dm.LIMITED


def test_lapsed_read_only_is_not_called_probation(client, make_user):
    lapsed, viewer = make_user("full", status=User.Status.READ_ONLY), make_user("full")
    enrol_totp(viewer)
    client.force_login(viewer)
    page = client.get(f"/members/{lapsed.slug}/").content.decode()
    assert "read-only (lapsed)" in page and "Probation" not in page


def test_read_only_kind_migration(seeded, make_user):
    import importlib

    from django.apps import apps

    migration = importlib.import_module("moderation.migrations.0004_rename_read_only_to_probation")
    action = ModerationAction.objects.create(target_user=make_user("full"), kind="probation",
                                             initiated_by=make_user("admin"), internal_reason="x")
    ModerationAction.objects.filter(pk=action.pk).update(kind="read_only")
    migration.forward(apps, None)
    action.refresh_from_db()
    assert action.kind == "probation"


# --- public summary ------------------------------------------------------------------------


def test_approver_may_edit_the_summary_and_both_are_kept(make_user):
    mod, target = make_user("moderator"), make_user("full")
    action = moderation.initiate_action(mod, target, "warning", internal_reason="x", public_summary="Rude to Sam")
    moderation.approve_action(make_user("moderator"), action, public_summary="Rude to another member")
    action.refresh_from_db()
    assert (action.public_summary_draft, action.public_summary) == ("Rude to Sam", "Rude to another member")


def test_draft_is_published_when_unedited(make_user):
    action = moderation.initiate_action(make_user("admin"), make_user("full"), "warning", internal_reason="x",
                                        public_summary="Spam")
    assert action.public_summary == "Spam"


# --- expiry --------------------------------------------------------------------------------


def test_action_stops_applying_before_the_expiry_job_runs(make_user, general):
    target = make_user("full")
    action = moderation.initiate_action(make_user("admin"), target, "suspension", internal_reason="x",
                                        ends_at=timezone.now() + timedelta(days=1))
    thread = make_thread(general, make_user("full"))
    assert not can(target, "thread.reply", thread)
    ModerationAction.objects.filter(pk=action.pk).update(ends_at=timezone.now() - timedelta(seconds=1))
    assert can(target, "thread.reply", thread)
    action.refresh_from_db()
    assert action.status == Status.ACTIVE
    assert moderation.expire_actions() == 1
    action.refresh_from_db()
    assert action.status == Status.EXPIRED
    assert AuditEntry.objects.filter(action="moderation.expire").exists()
    assert action in _record(target)


def test_expire_command(make_user):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("expire_actions", stdout=out)
    assert "Expired 0" in out.getvalue()


# --- bans ----------------------------------------------------------------------------------


def test_lifting_a_ban_restores_previous_standing(make_user, general):
    target = make_user("tenured")
    status_before, role_before = target.status, roles.trust_role(target)
    ban = moderation.initiate_action(make_user("admin"), target, "ban", internal_reason="x", public_summary="Abuse")
    target.refresh_from_db()
    assert target.status == status_before  # bans do not touch status or roles
    assert not can(target, "subforum.read", general)
    with pytest.raises(PermissionDenied):
        moderation.lift_ban(make_user("moderator"), ban, "")
    moderation.lift_ban(make_user("admin"), ban, "Apology accepted")
    target.refresh_from_db()
    assert target.status == status_before and roles.trust_role(target) == role_before
    assert can(target, "subforum.read", general)
    ban.refresh_from_db()
    assert ban.status == Status.REVERSED and ban.ends_at is not None
    assert ban in _record(target)
    reversal = ModerationAction.objects.get(kind="ban_reversal")
    assert reversal.is_public is False and reversal.related_action == ban
    assert Notification.objects.filter(recipient=target, kind="moderation.ban_lifted").exists()


def test_record_shows_lifted_without_saying_how(client, make_user):
    target, viewer = make_user("full"), make_user("full")
    ban = moderation.initiate_action(make_user("admin"), target, "ban", internal_reason="x", public_summary="Abuse")
    moderation.lift_ban(make_user("owner"), ban, "Paid the fee")
    enrol_totp(viewer)
    client.force_login(viewer)
    page = client.get(f"/members/{target.slug}/").content.decode()
    assert "lifted" in page and "Paid the fee" not in page


def test_member_is_told_when_action_is_taken(make_user):
    target = make_user("full")
    moderation.initiate_action(make_user("admin"), target, "warning", internal_reason="x")
    assert Notification.objects.filter(recipient=target, kind="moderation.action").exists()
    moderation.initiate_action(make_user("admin"), target, "note", internal_reason="private")
    assert Notification.objects.filter(recipient=target, kind="moderation.action").count() == 1


# --- pages ---------------------------------------------------------------------------------


def test_take_action_page_proposes_for_approval(client, make_user, general, scoped_mod):
    mod, target = scoped_mod(general), make_user("full")
    enrol_totp(mod)
    client.force_login(mod)
    page = client.get(f"/staff/members/{target.slug}/act/")
    assert page.status_code == 200 and b"General Discussion" in page.content
    client.post(f"/staff/members/{target.slug}/act/", {
        "kind": "suspension", "scope": general.pk, "days": "2", "internal_reason": "flaming", "public_summary": "Flaming",
    })
    action = ModerationAction.objects.get(target_user=target)
    assert action.status == Status.PENDING and action.scope_subforum == general
    assert client.get(f"/staff/members/{make_user('moderator').slug}/act/").status_code == 403
