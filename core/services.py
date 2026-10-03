from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from audit import log
from core.models import SiteSetting
from core.permissions import can


def require(actor, action, target=None):
    decision = can(actor, action, target)
    if not decision:
        raise PermissionDenied(decision.reason)
    return decision


@transaction.atomic
def set_site_setting(actor, key, value):
    require(actor, "site_setting.write", key)
    row = SiteSetting.objects.filter(key=key).first()
    old = row.value if row else None
    if row is None:
        row = SiteSetting(key=key)
    row.value, row.updated_by, row.updated_at = value, actor, timezone.now()
    row.save()
    log.record(actor, "site_setting.write", row, {"key": key, "old": old, "new": value})
    if key.startswith("sponsorship.cap."):
        from sponsorship.capacity import reassign_all

        reassign_all()
    return row
