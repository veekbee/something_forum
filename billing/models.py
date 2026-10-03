from django.conf import settings
from django.db import models
from django.utils import timezone


class Subscription(models.Model):
    """Stripe holds the card data; the forum keeps a customer reference and a status."""

    class Status(models.TextChoices):
        NONE = "none"
        ACTIVE = "active"
        PAST_DUE = "past_due"
        LAPSED = "lapsed"
        COMPED = "comped"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subscription")
    stripe_customer_id = models.CharField(max_length=64, blank=True)
    stripe_subscription_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NONE)
    current_period_end = models.DateTimeField(null=True, blank=True)
    # Period end plus billing.lapse_grace_days; a job makes the account read-only when it passes.
    read_only_at = models.DateTimeField(null=True, blank=True)
    # A comped subscription has no Stripe ids and never lapses.
    comped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    comped_at = models.DateTimeField(null=True, blank=True)


class Charge(models.Model):
    class Kind(models.TextChoices):
        SUBSCRIPTION = "subscription"
        BAN_REVERSAL = "ban_reversal"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="charges")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    stripe_payment_intent_id = models.CharField(max_length=64, unique=True)
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="usd")
    status = models.CharField(max_length=32)
    related_action = models.ForeignKey(
        "moderation.ModerationAction", null=True, blank=True, on_delete=models.PROTECT, related_name="charges"
    )
    created_at = models.DateTimeField(default=timezone.now)
