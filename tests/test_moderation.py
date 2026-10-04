"""Two-person rule for Moderators; Admins and Owners act alone (design rule 2)."""

import pytest
from django.core.exceptions import PermissionDenied

from audit.models import AuditEntry
from core.permissions import can
from moderation import services
from moderation.models import ModerationAction
from tests.factories import grant, make_post, make_thread


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admin_or_owner_acts_alone(make_user, role):
    actor, target = make_user(role), make_user("full")
    action = services.initiate_action(actor, target, "ban", internal_reason="spam", public_summary="Spam")
    assert action.status == ModerationAction.Status.ACTIVE
    assert action.initiated_by == actor
    assert action.approved_by is None
    assert ModerationAction.objects.in_force().filter(target_user=target, kind="ban").exists()
    assert not can(target, "search.use")


def test_moderator_action_waits_for_second_approver(make_user):
    mod, target = make_user("moderator"), make_user("full")
    action = services.initiate_action(mod, target, "warning", internal_reason="tone")
    assert action.status == ModerationAction.Status.PENDING
    with pytest.raises(PermissionDenied):
        services.approve_action(mod, action)
    services.approve_action(make_user("moderator"), action)
    action.refresh_from_db()
    assert action.status == ModerationAction.Status.ACTIVE
    assert action.approved_by_id != action.initiated_by_id


def test_ban_approver_must_be_admin_or_owner(make_user):
    mod, target = make_user("moderator"), make_user("full")
    ban = services.initiate_action(mod, target, "ban", internal_reason="abuse")
    with pytest.raises(PermissionDenied):
        services.approve_action(make_user("moderator"), ban)
    services.approve_action(make_user("admin"), ban)
    assert ModerationAction.objects.in_force().filter(target_user=target, kind="ban").exists()
    assert not can(target, "search.use")


def test_non_staff_cannot_initiate(make_user):
    with pytest.raises(PermissionDenied):
        services.initiate_action(make_user("tenured"), make_user("full"), "warning", internal_reason="x")


def test_moderator_cannot_take_admin_only_actions(make_user):
    with pytest.raises(PermissionDenied):
        services.initiate_action(
            make_user("moderator"), make_user("full"), "sponsoring_suspension", internal_reason="x"
        )


def test_cannot_act_on_equal_or_higher_rank(make_user):
    with pytest.raises(PermissionDenied):
        services.initiate_action(make_user("moderator"), make_user("moderator"), "warning", internal_reason="x")
    with pytest.raises(PermissionDenied):
        services.initiate_action(make_user("admin"), make_user("admin"), "warning", internal_reason="x")
    services.initiate_action(make_user("owner"), make_user("admin"), "warning", internal_reason="x")


def test_scoped_moderator_acts_only_in_scope(make_user, general, serious):
    mod = make_user("tenured")
    grant(mod, "moderator", scope_subforum=serious)
    target = make_user("full")
    in_scope = make_post(make_thread(serious, target), target)
    out_of_scope = make_post(make_thread(general, target), target)
    with pytest.raises(PermissionDenied):
        services.initiate_action(mod, target, "warning", internal_reason="x", related_post=out_of_scope)
    with pytest.raises(PermissionDenied):
        services.initiate_action(mod, target, "warning", internal_reason="x")
    action = services.initiate_action(mod, target, "warning", internal_reason="x", related_post=in_scope)
    assert action.status == ModerationAction.Status.PENDING


def test_staff_note_is_private(make_user):
    action = services.initiate_action(make_user("admin"), make_user("full"), "note", internal_reason="watch")
    assert action.is_public is False


def test_moderator_note_needs_no_approver_and_notifies_leadership(make_user, owner):
    from core.models import Notification

    admin, mod = make_user("admin"), make_user("moderator")
    note = services.initiate_action(mod, make_user("full"), "note", internal_reason="watch this one")
    assert note.status == ModerationAction.Status.ACTIVE and note.approved_by is None
    recipients = set(Notification.objects.filter(kind="moderation.note").values_list("recipient", flat=True))
    assert recipients == {admin.pk, owner.pk}
    assert Notification.objects.get(recipient=admin).payload["action"] == note.pk


def test_admin_note_does_not_notify_its_author(make_user, owner):
    from core.models import Notification

    admin = make_user("admin")
    services.initiate_action(admin, make_user("full"), "note", internal_reason="x")
    assert set(Notification.objects.values_list("recipient", flat=True)) == {owner.pk}


def test_actions_are_audited_in_the_same_transaction(make_user):
    mod, target = make_user("moderator"), make_user("full")
    action = services.initiate_action(mod, target, "warning", internal_reason="x")
    services.approve_action(make_user("admin"), action)
    actions = list(
        AuditEntry.objects.filter(target_type="moderation.moderationaction", target_id=str(action.pk))
        .order_by("pk").values_list("action", flat=True)
    )
    assert actions == ["moderation.initiate", "moderation.activate"]


def test_failed_action_leaves_no_audit_entry(make_user):
    before = AuditEntry.objects.count()
    with pytest.raises(PermissionDenied):
        services.initiate_action(make_user("full"), make_user("full"), "warning", internal_reason="x")
    assert AuditEntry.objects.count() == before
