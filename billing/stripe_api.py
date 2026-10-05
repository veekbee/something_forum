"""Every call the forum makes to Stripe goes through here, so tests can stand in for Stripe and
nothing else imports the SDK. The forum never sees card data: Checkout and the Customer Portal are
Stripe's hosted pages."""

import stripe
from django.conf import settings


def _client():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def create_checkout_session(**params):
    return _client().checkout.Session.create(**params)


def create_portal_session(customer, return_url):
    return _client().billing_portal.Session.create(customer=customer, return_url=return_url)


def refund(payment_intent_id):
    return _client().Refund.create(payment_intent=payment_intent_id)


def cancel_subscription(subscription_id):
    return _client().Subscription.cancel(subscription_id)


def delete_customer(customer_id):
    """Erasure (rule 80). A customer Stripe no longer has counts as deleted."""
    try:
        return _client().Customer.delete(customer_id)
    except stripe.InvalidRequestError as exc:
        if getattr(exc, "code", None) != "resource_missing":
            raise
        return None


def verify_webhook(payload, signature):
    """Raises stripe.SignatureVerificationError if the payload was not signed with our secret."""
    stripe.WebhookSignature.verify_header(payload, signature, settings.STRIPE_WEBHOOK_SECRET, tolerance=300)
