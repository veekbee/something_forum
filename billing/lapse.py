"""The lapse clock and comps (docs/DESIGN.md, Comps and Lapsing and renewal; rules 42 to 44).

The forum's clock governs. read_only_at is the end of the paid period or comp plus
billing.lapse_grace_days; then the account is read-only (lapsed) and the Stripe subscription is
cancelled, so coming back is a fresh year. After billing.lapse_restrict_days of lapse, access
narrows. No account is ever removed for lapsing.

Interim (sponsorship-transfer-brief.md): reaching the restriction stage is recorded, but a lapsed
sponsor's sponsees are not yet moved into sponsorship transfer, which the design has not specified.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts import roles
from accounts.models import User
from audit import log
from billing import stripe_api
from billing.membership import set_clock
from billing.models import LapsePeriod, Subscription
from core import registry
from core.models import Notification
from core.services import require

STAFF_ROLES = (roles.OWNER, roles.ADMIN, roles.MODERATOR)


# --- comps ---------------------------------------------------------------------------------


def holds_staff_role(user):
    from accounts.models import RoleAssignment

    return RoleAssignment.objects.filter(user=user, revoked_at__isnull=True, role__name__in=STAFF_ROLES).exists()


def start_staff_comp(user, actor=None):
    """Granting a staff role comps the member while they hold it (rule 42)."""
    sub, _ = Subscription.objects.select_for_update().get_or_create(user=user)
    sub.status, sub.comp_reason, sub.comped_until = Subscription.Status.COMPED, Subscription.CompReason.STAFF, None
    sub.comped_by, sub.comped_at = actor, timezone.now()
    set_clock(sub, None)
    sub.save()
    LapsePeriod.objects.filter(user=user, ended_at__isnull=True).update(ended_at=timezone.now())
    if user.status == User.Status.READ_ONLY:
        User.objects.filter(pk=user.pk).update(status=User.Status.ACTIVE)
    log.record(actor, "billing.staff_comp_start", user)


def end_staff_comp(user, actor=None, keep_comped=False):
    """When someone leaves staff the staff comp ends and lapse rules apply from that moment, unless an
    Admin or Owner keeps them comped as a deliberate choice."""
    sub = Subscription.objects.select_for_update().filter(user=user, comp_reason=Subscription.CompReason.STAFF).first()
    if sub is None or holds_staff_role(user):
        return
    now = timezone.now()
    if keep_comped:
        sub.comp_reason, sub.comped_by, sub.comped_at = Subscription.CompReason.OTHER, actor, now
        sub.save()
        log.record(actor, "billing.comp_kept", user)
        return
    sub.comp_reason = ""
    paid_until = sub.current_period_end if sub.current_period_end and sub.current_period_end > now else now
    sub.status = Subscription.Status.ACTIVE if paid_until > now else Subscription.Status.LAPSED
    set_clock(sub, paid_until)
    sub.save()
    log.record(actor, "billing.staff_comp_end", user, {"read_only_at": sub.read_only_at.isoformat()})


@transaction.atomic
def extend_comp(actor, member, until):
    """Only an Owner extends a comp (rule 42)."""
    require(actor, "billing.extend_comp", member)
    sub = Subscription.objects.select_for_update().get(user=member, status=Subscription.Status.COMPED)
    sub.comped_until = until
    sub.save(update_fields=["comped_until"])
    log.record(actor, "billing.comp_extend", member, {"until": until.isoformat() if until else None})
    return sub


@transaction.atomic
def launch(actor=None, now=None):
    """Mark the day billing goes live: every comp that exists then, other than a staff comp, becomes
    a founding comp ending billing.founding_comp_months later. Runs once."""
    from audit.models import AuditEntry
    from core.dates import add_months

    if AuditEntry.objects.filter(action="billing.launch").exists():
        return 0
    now = now or timezone.now()
    until = add_months(now, registry.site_value("billing.founding_comp_months"))
    comps = Subscription.objects.select_for_update().filter(status=Subscription.Status.COMPED).exclude(
        comp_reason=Subscription.CompReason.STAFF
    )
    count = comps.update(comp_reason=Subscription.CompReason.FOUNDING, comped_until=until)
    anchor = actor or User.objects.order_by("pk").first()
    if anchor is not None:
        log.record(actor, "billing.launch", anchor, {"founding_comps": count, "until": until.isoformat()})
    return count


# --- the daily job -------------------------------------------------------------------------


def _once(user, kind, key):
    """Send an account notification at most once for a given date or period."""
    if Notification.objects.filter(recipient=user, kind=kind, payload__key=key).exists():
        return False
    Notification.objects.create(recipient=user, kind=kind, payload={"key": key})
    return True


def _remind(now):
    sent = 0
    renewal = now + timedelta(days=registry.site_value("billing.renewal_reminder_days"))
    for sub in Subscription.objects.filter(status=Subscription.Status.ACTIVE, cancel_at_period_end=False,
                                           current_period_end__gt=now, current_period_end__lte=renewal
                                           ).exclude(stripe_subscription_id="").select_related("user"):
        sent += _once(sub.user, "billing.renewal_reminder", sub.current_period_end.date().isoformat())
    soon = now + timedelta(days=registry.site_value("billing.read_only_warning_days"))
    for sub in Subscription.objects.filter(read_only_at__gt=now, read_only_at__lte=soon).exclude(
        status=Subscription.Status.COMPED
    ).select_related("user"):
        if sub.user.status != User.Status.READ_ONLY:
            sent += _once(sub.user, "billing.read_only_soon", sub.read_only_at.date().isoformat())
    ending = now + timedelta(days=registry.site_value("billing.founding_comp_warning_days"))
    for sub in Subscription.objects.filter(status=Subscription.Status.COMPED,
                                           comp_reason=Subscription.CompReason.FOUNDING,
                                           comped_until__gt=now, comped_until__lte=ending).select_related("user"):
        sent += _once(sub.user, "billing.founding_comp_ending", sub.comped_until.date().isoformat())
    return sent


def _end_comps(now):
    count = 0
    for pk in Subscription.objects.filter(status=Subscription.Status.COMPED, comped_until__lte=now).values_list("pk", flat=True):
        with transaction.atomic():
            sub = Subscription.objects.select_for_update().get(pk=pk)
            if sub.status != Subscription.Status.COMPED or sub.comped_until is None or sub.comped_until > now:
                continue
            sub.status, ended = Subscription.Status.LAPSED, sub.comped_until
            set_clock(sub, ended)
            sub.save()
            log.record(None, "billing.comp_end", sub.user, {"comped_until": ended.isoformat()})
            count += 1
    return count


def _go_read_only(now):
    count = 0
    for pk in Subscription.objects.filter(read_only_at__lte=now).exclude(status=Subscription.Status.COMPED).values_list("pk", flat=True):
        with transaction.atomic():
            sub = Subscription.objects.select_for_update().select_related("user").get(pk=pk)
            user = sub.user
            if sub.status == Subscription.Status.COMPED or sub.read_only_at is None or sub.read_only_at > now:
                continue
            if user.status != User.Status.ACTIVE or holds_staff_role(user):
                continue
            User.objects.filter(pk=user.pk).update(status=User.Status.READ_ONLY)
            LapsePeriod.objects.get_or_create(user=user, ended_at=None, defaults={"started_at": sub.read_only_at})
            if sub.stripe_subscription_id:
                # Returning means a fresh Checkout and a new year, never paying for time away.
                stripe_api.cancel_subscription(sub.stripe_subscription_id)
                sub.stripe_subscription_id = ""
            sub.status = Subscription.Status.LAPSED
            sub.save()
            log.record(None, "billing.read_only", user, {"read_only_at": sub.read_only_at.isoformat()})
            Notification.objects.create(recipient=user, kind="billing.read_only", payload={})
            count += 1
    return count


def _restrict(now):
    days = registry.site_value("billing.lapse_restrict_days")
    count = 0
    for lapse in LapsePeriod.objects.filter(ended_at__isnull=True, restricted_at__isnull=True,
                                           started_at__lte=now - timedelta(days=days)):
        with transaction.atomic():
            LapsePeriod.objects.filter(pk=lapse.pk).update(restricted_at=now)
            log.record(None, "billing.restricted", lapse.user)
            count += 1
    return count


def run_daily(now=None):
    """Reminders, comp endings, going read-only and the restriction stage, in that order."""
    now = now or timezone.now()
    return {
        "reminders": _remind(now),
        "comps_ended": _end_comps(now),
        "read_only": _go_read_only(now),
        "restricted": _restrict(now),
    }


def is_restricted(user):
    """Past the restriction stage of a lapse: access narrows (rule 44)."""
    return user.status == User.Status.READ_ONLY and LapsePeriod.objects.filter(
        user=user, ended_at__isnull=True, restricted_at__isnull=False
    ).exists()


def lapsed_days_since(user, since, now=None):
    """Days lapsed between `since` and now, for pausing the Provisional clock."""
    now = now or timezone.now()
    total = timedelta(0)
    for period in LapsePeriod.objects.filter(user=user).exclude(ended_at__lt=since):
        start = max(period.started_at, since)
        end = min(period.ended_at or now, now)
        if end > start:
            total += end - start
    return total
