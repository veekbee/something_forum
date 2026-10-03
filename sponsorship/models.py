from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.immutability import HistoryRowMixin


class Invitation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        WAITLISTED = "waitlisted"
        APPROVED = "approved"
        DECLINED = "declined"
        EXPIRED = "expired"

    sponsor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invitations_sent")
    invitee_email = models.EmailField()
    # Only a hash is stored; the token itself goes to the invitee by email.
    token_hash = models.CharField(max_length=64, unique=True)
    vouching_notes = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)


class Sponsorship(HistoryRowMixin, models.Model):
    """A sponsor vouching for a member. Active while ended_at is null. The pedigree is the set of
    is_original rows; a transfer closes one row and opens another with `previous` pointing back."""

    class EndReason(models.TextChoices):
        TENURED = "tenured"
        TRANSFERRED = "transferred"
        SPONSOR_LEFT = "sponsor_left"
        SPONSOR_BANNED = "sponsor_banned"
        MEMBER_REMOVED = "member_removed"

    closing_fields = ("ended_at", "end_reason")

    sponsor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sponsorships_given")
    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sponsorships")
    sponsor_role = models.ForeignKey("accounts.Role", on_delete=models.PROTECT, related_name="+")
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=16, choices=EndReason.choices, blank=True)
    previous = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="next"
    )
    is_original = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["member"], condition=Q(ended_at__isnull=True), name="one_active_sponsorship_per_member"
            ),
            models.UniqueConstraint(
                fields=["member"], condition=Q(is_original=True), name="one_original_sponsorship_per_member"
            ),
            models.CheckConstraint(condition=~Q(sponsor=models.F("member")), name="no_self_sponsorship"),
        ]
        indexes = [
            models.Index(fields=["sponsor"], condition=Q(ended_at__isnull=True), name="sponsorship_active_by_sponsor")
        ]


class Promotion(models.Model):
    """The Provisional to Full (and Full to Tenured) workflow record. Eligibility is computed."""

    class Status(models.TextChoices):
        RECOMMENDED = "recommended"
        REVIEWED = "reviewed"
        APPROVED = "approved"
        DECLINED = "declined"

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="promotions")
    from_role = models.ForeignKey("accounts.Role", on_delete=models.PROTECT, related_name="+")
    to_role = models.ForeignKey("accounts.Role", on_delete=models.PROTECT, related_name="+")
    recommended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    recommended_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECOMMENDED)
    created_at = models.DateTimeField(default=timezone.now)
