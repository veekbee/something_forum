"""DMs: participants, Admins and Owners read; Moderators only under an unexpired grant, audited."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from audit.models import AuditEntry
from boards.services import open_thread
from core.permissions import can
from moderation.services import grant_dm_access
from tests.factories import make_dm


@pytest.fixture
def dm(make_user):
    return make_dm(make_user("full"), make_user("provisional"))


def _participants(dm):
    return [p.user for p in dm.participants.all()]


def test_participants_read(dm):
    for user in _participants(dm):
        assert can(user, "thread.read", dm)


def test_other_members_do_not(make_user, dm):
    assert not can(make_user("tenured"), "thread.read", dm)


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admin_and_owner_read(make_user, dm, role):
    assert can(make_user(role), "thread.read", dm)


def test_moderator_without_grant_is_denied(make_user, dm):
    assert not can(make_user("moderator"), "thread.read", dm)


def test_moderator_with_grant_reads_and_is_audited(make_user, dm):
    mod, admin = make_user("moderator"), make_user("admin")
    grant = grant_dm_access(
        admin, mod, [_participants(dm)[1]], case_note="Report 12", expires_at=timezone.now() + timedelta(days=3)
    )
    decision = can(mod, "thread.read", dm)
    assert decision and decision.via == "dm_grant" and decision.detail == grant

    open_thread(mod, dm, ip="203.0.113.5")
    entry = AuditEntry.objects.get(action="dm.read_under_grant")
    assert entry.actor == mod and entry.target_id == str(dm.pk) and entry.payload == {"grant": grant.pk}
    assert AuditEntry.objects.filter(action="dm_grant.create", actor=admin).exists()


def test_participant_reads_are_not_audited(dm):
    open_thread(_participants(dm)[0], dm)
    assert not AuditEntry.objects.filter(action="dm.read_under_grant").exists()


def test_grant_for_other_members_does_not_cover(make_user, dm):
    mod = make_user("moderator")
    grant_dm_access(make_user("admin"), mod, [make_user("full")], case_note="x",
                    expires_at=timezone.now() + timedelta(days=1))
    assert not can(mod, "thread.read", dm)


def test_expired_or_revoked_grant(make_user, dm):
    mod = make_user("moderator")
    grant = grant_dm_access(make_user("admin"), mod, _participants(dm), case_note="x",
                            expires_at=timezone.now() + timedelta(days=1))
    grant.expires_at = timezone.now() - timedelta(seconds=1)
    grant.save()
    assert not can(mod, "thread.read", dm)
    grant.expires_at = timezone.now() + timedelta(days=1)
    grant.revoked_at = timezone.now()
    grant.save()
    assert not can(mod, "thread.read", dm)


def test_only_admin_or_owner_grants_and_only_to_moderators(make_user, dm):
    with pytest.raises(PermissionDenied):
        grant_dm_access(make_user("moderator"), make_user("moderator"), _participants(dm), case_note="x",
                        expires_at=timezone.now() + timedelta(days=1))
    with pytest.raises(PermissionDenied):
        grant_dm_access(make_user("admin"), make_user("tenured"), _participants(dm), case_note="x",
                        expires_at=timezone.now() + timedelta(days=1))


def test_open_thread_refuses_without_access(make_user, dm):
    with pytest.raises(PermissionDenied):
        open_thread(make_user("moderator"), dm)
