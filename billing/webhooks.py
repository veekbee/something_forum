"""Stripe webhooks (rule 50): each event processed at most once, keyed by its id, in the same
transaction as its effects. Handled: checkout completed (subscriptions and one-time payments),
invoice paid, invoice payment failed, subscription updated, subscription deleted. Everything else
is recorded and ignored. Events may arrive more than once and out of order."""

from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from billing import membership
from billing.models import Charge, StripeEvent, Subscription
from core.models import Notification

# One-time Checkout purposes (ban payments, gifts, extras) register a handler here.
PURPOSES = {}


def register_purpose(name):
    def wrap(fn):
        PURPOSES[name] = fn
        return fn

    return wrap


def _ts(value):
    return datetime.fromtimestamp(value, tz=dt_timezone.utc) if value else None


def _user_from_metadata(meta):
    user_id = (meta or {}).get("user")
    return User.objects.filter(pk=int(user_id)).first() if user_id else None


def _subscription_row(subscription_id=None, customer_id=None):
    sub = None
    if subscription_id:
        sub = Subscription.objects.filter(stripe_subscription_id=subscription_id).select_related("user").first()
    if sub is None and customer_id:
        sub = Subscription.objects.filter(stripe_customer_id=customer_id).select_related("user").first()
    return sub


def _invoice_subscription(invoice):
    """Newer API versions nest the subscription under parent.subscription_details."""
    details = (invoice.get("parent") or {}).get("subscription_details") or {}
    return invoice.get("subscription") or details.get("subscription"), details.get("metadata") or {}


def _invoice_period_end(invoice):
    ends = [line.get("period", {}).get("end") for line in invoice.get("lines", {}).get("data", [])]
    ends = [e for e in ends if e]
    return _ts(max(ends)) if ends else None


def checkout_completed(session):
    if session.get("payment_status") not in ("paid", "no_payment_required"):
        return
    meta = session.get("metadata") or {}
    purpose = meta.get("purpose")
    user = _user_from_metadata(meta)
    if user is None:
        return
    if purpose == "membership":
        membership.restore(
            user, stripe_customer_id=session.get("customer") or "",
            stripe_subscription_id=session.get("subscription") or "", reason="checkout",
        )
    elif purpose in PURPOSES:
        PURPOSES[purpose](user, session)


def invoice_paid(invoice):
    subscription_id, meta = _invoice_subscription(invoice)
    sub = _subscription_row(subscription_id, invoice.get("customer"))
    user = sub.user if sub else _user_from_metadata(meta)
    if user is None:
        return
    membership.restore(
        user, _invoice_period_end(invoice), stripe_customer_id=invoice.get("customer") or "",
        stripe_subscription_id=subscription_id or "", reason="invoice",
    )
    reference = invoice.get("payment_intent") or invoice.get("id")
    Charge.objects.get_or_create(
        stripe_payment_intent_id=reference,
        defaults={"user": user, "kind": Charge.Kind.SUBSCRIPTION, "amount_cents": invoice.get("amount_paid", 0),
                  "currency": invoice.get("currency", "usd"), "status": "succeeded"},
    )


def invoice_payment_failed(invoice):
    subscription_id, meta = _invoice_subscription(invoice)
    sub = _subscription_row(subscription_id, invoice.get("customer"))
    if sub is None:
        return
    if sub.status == Subscription.Status.ACTIVE:
        sub.status = Subscription.Status.PAST_DUE
        sub.save(update_fields=["status"])
    Notification.objects.create(recipient=sub.user, kind="billing.payment_failed", payload={})


def subscription_updated(obj):
    sub = _subscription_row(obj.get("id"), obj.get("customer"))
    if sub is None:
        return
    items = (obj.get("items") or {}).get("data") or [{}]
    period_end = _ts(obj.get("current_period_end") or items[0].get("current_period_end"))
    sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    if period_end and (sub.current_period_end is None or period_end > sub.current_period_end):
        sub.current_period_end = period_end
    if sub.status != Subscription.Status.COMPED:
        if obj.get("status") in ("past_due", "unpaid"):
            sub.status = Subscription.Status.PAST_DUE
        membership.set_clock(sub, sub.current_period_end)
    sub.save()


def subscription_deleted(obj):
    """Stripe has stopped billing. The forum's clock, from the end of the paid period, still decides."""
    sub = _subscription_row(obj.get("id"), obj.get("customer"))
    if sub is None:
        return
    sub.stripe_subscription_id, sub.cancel_at_period_end = "", False
    sub.save(update_fields=["stripe_subscription_id", "cancel_at_period_end"])


HANDLERS = {
    "checkout.session.completed": checkout_completed,
    "invoice.paid": invoice_paid,
    "invoice.payment_failed": invoice_payment_failed,
    "customer.subscription.updated": subscription_updated,
    "customer.subscription.deleted": subscription_deleted,
}


@transaction.atomic
def process(event):
    """Returns True if the event was handled now, False if it had been already."""
    row, _ = StripeEvent.objects.select_for_update().get_or_create(
        event_id=event["id"], defaults={"type": event.get("type", "")}
    )
    if row.processed_at is not None:
        return False
    handler = HANDLERS.get(event.get("type"))
    if handler is not None:
        handler(event["data"]["object"])
    row.processed_at = timezone.now()
    row.save(update_fields=["processed_at"])
    return True
