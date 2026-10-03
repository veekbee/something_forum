"""Append-only audit log, history rows that are closed rather than edited, soft-deleted posts."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.utils import timezone

from accounts import roles
from accounts.models import Role
from accounts.services import grant_role, revoke_role
from audit import log
from audit.models import AppendOnlyError, AuditEntry
from boards.models import Post
from tests.factories import grant, make_post, make_thread, sponsor


@pytest.fixture
def entry(owner):
    with transaction.atomic():
        return log.record(owner, "test.action", owner, {"k": "v"})


@pytest.mark.parametrize("sql", ["UPDATE audit_auditentry SET action = 'x'", "DELETE FROM audit_auditentry",
                                 "TRUNCATE audit_auditentry"])
def test_database_rejects_changes(entry, sql):
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic(), connection.cursor() as cursor:
            # Fire deferred foreign-key checks first, or Postgres refuses TRUNCATE for that reason.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(sql)


def test_model_has_no_update_or_delete_path(entry):
    entry.action = "changed"
    with pytest.raises(AppendOnlyError):
        entry.save()
    with pytest.raises(AppendOnlyError):
        entry.delete()
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.all().delete()
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.update(action="x")


def test_record_requires_a_transaction(owner, monkeypatch):
    from types import SimpleNamespace

    from audit.log import AuditOutsideTransaction

    # pytest-django wraps every test in a transaction, so simulate autocommit for the guard.
    outside = SimpleNamespace(get_connection=lambda *args: SimpleNamespace(in_atomic_block=False))
    monkeypatch.setattr(log, "transaction", outside)
    with pytest.raises(AuditOutsideTransaction):
        log.record(owner, "x", owner)


def test_role_change_is_a_new_row(make_user):
    member = make_user("provisional")
    assignment = member.role_assignments.get()
    assignment.role = Role.objects.get(name="full")
    with pytest.raises(ValidationError):
        assignment.save()
    with pytest.raises(ValidationError):
        member.role_assignments.get().delete()


def test_revoke_and_grant(owner, make_user):
    member = make_user("provisional")
    admin = make_user("admin")
    revoke_role(admin, member.role_assignments.get())
    grant_role(admin, member, "full")
    assert roles.trust_role(member).name == "full"
    assert member.role_assignments.count() == 2
    assert AuditEntry.objects.filter(action__in=["role.grant", "role.revoke"]).count() == 2


def test_revocation_cannot_be_undone(make_user):
    member = make_user("full")
    assignment = member.role_assignments.get()
    assignment.revoked_at = timezone.now()
    assignment.save()
    assignment.revoked_at = None
    with pytest.raises(ValidationError):
        assignment.save()


def test_trust_role_is_highest_global_assignment(make_user, general):
    member = make_user("full")
    grant(member, "tenured")
    grant(member, "moderator", scope_subforum=general)
    assert roles.trust_role(member).name == "tenured"


def test_only_owner_appoints_admins_and_owners(make_user):
    target = make_user("tenured")
    with pytest.raises(PermissionDenied):
        grant_role(make_user("admin"), target, "admin")
    grant_role(make_user("owner"), target, "admin")
    with pytest.raises(PermissionDenied):
        grant_role(make_user("admin"), make_user("tenured"), "owner")


def test_moderators_are_appointed_from_tenured(make_user):
    admin = make_user("admin")
    with pytest.raises(PermissionDenied):
        grant_role(admin, make_user("full"), "moderator")
    grant_role(admin, make_user("tenured"), "moderator")


def test_sponsorship_change_is_a_new_row(make_user):
    row = sponsor(make_user("full"), make_user("provisional"))
    row.sponsor = make_user("tenured")
    with pytest.raises(ValidationError):
        row.save()


def test_posts_are_soft_deleted_only(make_user, general):
    author = make_user("full")
    post = make_post(make_thread(general, author), author)
    with pytest.raises(ValidationError):
        post.delete()
    with pytest.raises(ValidationError):
        Post.objects.filter(pk=post.pk).delete()
