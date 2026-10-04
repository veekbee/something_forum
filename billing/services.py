"""Starting payments. Stripe's hosted Checkout and Customer Portal take the card; the forum learns
the result from webhooks (billing.webhooks)."""

from django.conf import settings
from django.db import transaction

from billing import stripe_api
from billing.models import Subscription
from core.services import require


def _customer_params(user, sub):
    if sub.stripe_customer_id:
        return {"customer": sub.stripe_customer_id, "customer_update": {"address": "auto", "name": "auto"}}
    return {"customer_email": user.email, "customer_creation": "always"}


def checkout(user, *, purpose, line_items, mode, success_url, cancel_url, metadata=None):
    """A Stripe Checkout session; returns its URL. Stripe Tax needs a billing address."""
    with transaction.atomic():
        sub, _ = Subscription.objects.get_or_create(user=user)
    meta = {"purpose": purpose, "user": str(user.pk), **(metadata or {})}
    params = {
        "mode": mode,
        "line_items": line_items,
        "client_reference_id": str(user.pk),
        "metadata": meta,
        "automatic_tax": {"enabled": True},
        "billing_address_collection": "required",
        "success_url": success_url,
        "cancel_url": cancel_url,
        **_customer_params(user, sub),
    }
    if mode == "subscription":
        params["subscription_data"] = {"metadata": meta}
        params.pop("customer_creation", None)
    else:
        params["payment_intent_data"] = {"metadata": meta}
    return stripe_api.create_checkout_session(**params)["url"]


def membership_checkout(user, success_url, cancel_url):
    """$25 a year, annual only, no trial (rule 40); the price id comes from configuration."""
    require(user, "billing.pay_membership")
    return checkout(
        user, purpose="membership", mode="subscription",
        line_items=[{"price": settings.STRIPE_PRICE_MEMBERSHIP, "quantity": 1}],
        success_url=success_url, cancel_url=cancel_url,
    )


def portal_url(user, return_url):
    require(user, "billing.portal")
    sub = Subscription.objects.get(user=user)
    return stripe_api.create_portal_session(sub.stripe_customer_id, return_url)["url"]
