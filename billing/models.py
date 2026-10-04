from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Subscription(models.Model):
    """Stripe holds the card data; the forum keeps a customer reference, a status and its own lapse
    clock (docs/DESIGN.md, Lapsing and renewal; rule 43)."""

    class Status(models.TextChoices):
        NONE = "none"
        ACTIVE = "active"
        PAST_DUE = "past_due"
        LAPSED = "lapsed"
        COMPED = "comped"

    class CompReason(models.TextChoices):
        STAFF = "staff"
        FOUNDING = "founding"
        OTHER = "other"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subscription")
    stripe_customer_id = models.CharField(max_length=64, blank=True)
    stripe_subscription_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NONE)
    # End of the paid year (or of a gifted first year, which has no Stripe subscription).
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    # End of the paid period or comp plus billing.lapse_grace_days; then the account is read-only.
    read_only_at = models.DateTimeField(null=True, blank=True)
    # read_only_at plus billing.lapse_restrict_days; then access narrows.
    restricted_at = models.DateTimeField(null=True, blank=True)
    comped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    comped_at = models.DateTimeField(null=True, blank=True)
    comp_reason = models.CharField(max_length=16, choices=CompReason.choices, blank=True)
    # A founding comp ends here; a staff comp has none and ends with the staff role.
    comped_until = models.DateTimeField(null=True, blank=True)


class LapsePeriod(models.Model):
    """One row per lapse. The Provisional clock subtracts lapsed days, and the restriction stage is
    measured from started_at."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lapse_periods")
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    restricted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user"], condition=Q(ended_at__isnull=True), name="one_open_lapse_per_user")
        ]


class Charge(models.Model):
    class Kind(models.TextChoices):
        SUBSCRIPTION = "subscription"
        BAN_REVERSAL = "ban_reversal"
        EXTRA = "extra"
        GIFT = "gift"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="charges")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    stripe_payment_intent_id = models.CharField(max_length=128, unique=True)
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="usd")
    status = models.CharField(max_length=32)
    related_action = models.ForeignKey(
        "moderation.ModerationAction", null=True, blank=True, on_delete=models.PROTECT, related_name="charges"
    )
    created_at = models.DateTimeField(default=timezone.now)


class StripeEvent(models.Model):
    """Each webhook event is processed at most once, in the same transaction as its effects (rule 50)."""

    event_id = models.CharField(max_length=128, unique=True)
    type = models.CharField(max_length=128)
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)


class Gift(models.Model):
    """A sponsor paying their own approved Guest invitee's first year (rule 41)."""

    class Status(models.TextChoices):
        PAID = "paid"
        ACCEPTED = "accepted"

    sponsor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gifts_given")
    invitee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gifts_received")
    charge = models.OneToOneField(Charge, on_delete=models.PROTECT, related_name="gift")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PAID)
    created_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)


class Extra(models.Model):
    """The catalogue of paid extras: a new extra is a row, not new code. The price id comes from
    the setting named by stripe_price_setting (rule 40)."""

    key = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    stripe_price_setting = models.CharField(max_length=64)
    min_role = models.CharField(max_length=32, default="provisional")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Entitlement(models.Model):
    """An extra a member has bought: once, for good, unless revoked by a moderation action (rule 49)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="entitlements")
    extra = models.ForeignKey(Extra, on_delete=models.PROTECT, related_name="entitlements")
    charge = models.OneToOneField(Charge, on_delete=models.PROTECT, related_name="entitlement")
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by_action = models.ForeignKey(
        "moderation.ModerationAction", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
