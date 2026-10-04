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


def cancel_subscription(subscription_id):
    return _client().Subscription.cancel(subscription_id)


def verify_webhook(payload, signature):
    """Raises stripe.SignatureVerificationError if the payload was not signed with our secret."""
    stripe.WebhookSignature.verify_header(payload, signature, settings.STRIPE_WEBHOOK_SECRET, tolerance=300)
