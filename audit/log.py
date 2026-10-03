from django.db import transaction

from audit.models import AuditEntry


class AuditOutsideTransaction(Exception):
    pass


def record(actor, action, target, payload=None, ip=None):
    """Write an audit entry. Must be called inside the transaction.atomic() block that performs
    the action, so the entry and the action commit or roll back together (design rule 10)."""
    if not transaction.get_connection().in_atomic_block:
        raise AuditOutsideTransaction("audit.record must run inside the action's transaction")
    return AuditEntry.objects.create(
        actor=actor,
        action=action,
        target_type=target._meta.label_lower,
        target_id=str(target.pk),
        payload=payload or {},
        ip=ip,
    )
