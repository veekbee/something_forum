"""Ban payments (docs/DESIGN.md, Ban payments; rule 45). Every ordinary ban can be lifted by
payment; the fee is billing.ban_reversal_fee_cents, multiplied by billing.ban_reversal_fee_multiplier
once for each ban the member has already paid off. Payment lifts the ban only."""

from django.utils import timezone

from audit import log
from billing.models import Charge
from billing.services import checkout
from billing.webhooks import register_purpose
from core import registry
from core.models import Notification
from core.services import require
from moderation.models import ModerationAction


def paid_off_count(user):
    return Charge.objects.filter(user=user, kind=Charge.Kind.BAN_REVERSAL, status="succeeded").count()


def fee_cents(user):
    base = registry.site_value("billing.ban_reversal_fee_cents")
    return base * registry.site_value("billing.ban_reversal_fee_multiplier") ** paid_off_count(user)


def active_ban(user):
    return ModerationAction.objects.in_force().filter(target_user=user, kind=ModerationAction.Kind.BAN).first()


def ban_checkout(user, ban, success_url, cancel_url):
    require(user, "billing.pay_ban", ban)
    amount = fee_cents(user)
    return checkout(
        user, purpose="ban", mode="payment", success_url=success_url, cancel_url=cancel_url,
        line_items=[{"price_data": {"currency": "usd", "unit_amount": amount,
                                    "product_data": {"name": "Lifting a ban"}}, "quantity": 1}],
        metadata={"ban": str(ban.pk), "amount": str(amount)},
    )


@register_purpose("ban")
def ban_paid(user, session):
    """Record the payment and lift the ban. The record shows it as lifted, not how; staff see the
    payment and its amount in the audit log and the per-member view."""
    meta = session.get("metadata") or {}
    ban = ModerationAction.objects.select_for_update().filter(
        pk=int(meta.get("ban", 0)), target_user=user, kind=ModerationAction.Kind.BAN
    ).first()
    charge, created = Charge.objects.get_or_create(
        stripe_payment_intent_id=session.get("payment_intent") or session["id"],
        defaults={"user": user, "kind": Charge.Kind.BAN_REVERSAL, "amount_cents": session.get("amount_total", 0),
                  "currency": session.get("currency", "usd"), "status": "succeeded", "related_action": ban},
    )
    if not created or ban is None or ban.status != ModerationAction.Status.ACTIVE:
        return
    now = timezone.now()
    ban.status, ban.ends_at = ModerationAction.Status.REVERSED, now
    ban.save(update_fields=["status", "ends_at"])
    ModerationAction.objects.create(
        target_user=user, kind=ModerationAction.Kind.BAN_REVERSAL, initiated_by=user,
        status=ModerationAction.Status.ACTIVE, starts_at=now, internal_reason="Lifted by payment",
        is_public=False, related_action=ban,
    )
    log.record(None, "moderation.lift_ban_paid", ban, {"amount_cents": charge.amount_cents, "charge": charge.pk})
    Notification.objects.create(recipient=user, kind="moderation.ban_lifted", payload={"action": ban.pk})
