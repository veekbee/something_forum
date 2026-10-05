from django.db import transaction

from audit.models import AuditEntry


class AuditOutsideTransaction(Exception):
    pass


def record(actor, action, target, payload=None, ip=None, using="default"):
    """Write an audit entry. Must be called inside the transaction.atomic() block that performs
    the action, so the entry and the action commit or roll back together (design rule 10). A
    target of None records an action on the forum as a whole."""
    if not transaction.get_connection(using).in_atomic_block:
        raise AuditOutsideTransaction("audit.record must run inside the action's transaction")
    return AuditEntry.objects.using(using).create(
        actor=actor,
        action=action,
        target_type=target._meta.label_lower if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        payload=payload or {},
        ip=ip,
    )
