from django.db import transaction
from django.utils import timezone

from accounts.models import Role, RoleAssignment
from audit import log
from core.permissions import RoleChange
from core.services import require


@transaction.atomic
def grant_role(actor, user, role_name, scope_subforum=None, reason=""):
    require(actor, "role.grant", RoleChange(user, role_name, scope_subforum))
    assignment = RoleAssignment.objects.create(
        user=user, role=Role.objects.get(name=role_name), scope_subforum=scope_subforum, granted_by=actor, reason=reason
    )
    log.record(actor, "role.grant", assignment, {"user": user.pk, "role": role_name, "reason": reason})
    return assignment


@transaction.atomic
def revoke_role(actor, assignment, reason=""):
    require(actor, "role.revoke", RoleChange(assignment.user, assignment.role.name, assignment.scope_subforum))
    assignment.revoked_at, assignment.revoked_by = timezone.now(), actor
    assignment.save()
    log.record(actor, "role.revoke", assignment, {"user": assignment.user_id, "reason": reason})
    return assignment
