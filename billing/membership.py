"""What payment does to a member (docs/DESIGN.md, Membership and Lapsing and renewal; rules 41 and
43). The forum's own clock governs: read_only_at is the end of the paid period or comp plus
billing.lapse_grace_days, whatever Stripe's status says, and any payment restores at once."""

from datetime import timedelta

from django.utils import timezone

from accounts import roles
from accounts.models import Role, RoleAssignment, User
from audit import log
from billing.models import LapsePeriod, Subscription
from core import registry
from core.models import Notification


def subscription_for(user):
    sub, _ = Subscription.objects.select_for_update().get_or_create(user=user)
    return sub


def set_clock(sub, paid_until):
    """Recompute the lapse dates from the end of the paid period or comp."""
    if paid_until is None:
        sub.read_only_at = sub.restricted_at = None
        return
    sub.read_only_at = paid_until + timedelta(days=registry.site_value("billing.lapse_grace_days"))
    sub.restricted_at = sub.read_only_at + timedelta(days=registry.site_value("billing.lapse_restrict_days"))


def _promote_guest(user, actor=None):
    """Payment, a gift or a comp moves an approved Guest to Provisional, starting the Provisional period."""
    if user.status != User.Status.GUEST:
        return False
    role = roles.trust_role(user)
    if role is None or role.name != roles.GUEST:
        return False
    now = timezone.now()
    for assignment in RoleAssignment.objects.filter(user=user, role__name=roles.GUEST, revoked_at__isnull=True,
                                                    scope_subforum__isnull=True):
        assignment.revoked_at, assignment.revoked_by = now, actor
        assignment.save()
    RoleAssignment.objects.create(user=user, role=Role.objects.get(name=roles.PROVISIONAL), granted_by=actor,
                                  granted_at=now, reason="Membership paid")
    return True


def restore(user, paid_until=None, *, stripe_customer_id="", stripe_subscription_id="", reason="payment"):
    """A payment at any point restores the member at once: status active, the role they held, a
    running Provisional clock. Must run inside the transaction recording the payment."""
    sub = subscription_for(user)
    if stripe_customer_id:
        sub.stripe_customer_id = stripe_customer_id
    if stripe_subscription_id:
        sub.stripe_subscription_id = stripe_subscription_id
    if paid_until is not None and (sub.current_period_end is None or paid_until > sub.current_period_end):
        sub.current_period_end = paid_until
    if sub.status != Subscription.Status.COMPED:
        sub.status = Subscription.Status.ACTIVE
        set_clock(sub, sub.current_period_end)
    sub.save()
    now = timezone.now()
    lapsed = LapsePeriod.objects.filter(user=user, ended_at__isnull=True).update(ended_at=now)
    promoted = _promote_guest(user)
    was_read_only = user.status == User.Status.READ_ONLY
    if was_read_only or promoted:
        User.objects.filter(pk=user.pk).update(status=User.Status.ACTIVE)
        user.status = User.Status.ACTIVE
    log.record(None, "billing.restore", user, {"reason": reason, "promoted": promoted, "lapse_ended": bool(lapsed)})
    if was_read_only:
        Notification.objects.create(recipient=user, kind="billing.restored", payload={})
    return sub
