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
    from billing import lapse

    if role_name in lapse.STAFF_ROLES:
        lapse.start_staff_comp(user, actor)
    from sponsorship import transfers

    transfers.resume_for_sponsor(user, actor)
    return assignment


@transaction.atomic
def revoke_role(actor, assignment, reason="", keep_comped=False):
    """Revoking the last staff role ends the staff comp (rule 42) unless the revoking Admin or Owner
    keeps the member comped."""
    require(actor, "role.revoke", RoleChange(assignment.user, assignment.role.name, assignment.scope_subforum))
    assignment.revoked_at, assignment.revoked_by = timezone.now(), actor
    assignment.save()
    log.record(actor, "role.revoke", assignment, {"user": assignment.user_id, "reason": reason})
    from billing import lapse

    if assignment.role.name in lapse.STAFF_ROLES:
        lapse.end_staff_comp(assignment.user, actor, keep_comped=keep_comped)
    from sponsorship import transfers

    transfers.sponsor_lost_role(assignment.user, actor)
    return assignment
