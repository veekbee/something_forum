from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Report(models.Model):
    class Status(models.TextChoices):
        OPEN = "open"
        HANDLED = "handled"
        DISMISSED = "dismissed"

    post = models.ForeignKey("boards.Post", null=True, blank=True, on_delete=models.PROTECT, related_name="reports")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="reports_against"
    )
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports_made")
    reason = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(post__isnull=False) | Q(user__isnull=False), name="report_has_subject")
        ]


class ModerationActionQuerySet(models.QuerySet):
    def in_force(self, at=None):
        """Active actions whose period covers `at` (default now)."""
        at = at or timezone.now()
        return self.filter(status=ModerationAction.Status.ACTIVE, starts_at__lte=at).filter(
            Q(ends_at__isnull=True) | Q(ends_at__gt=at)
        )


class ModerationAction(models.Model):
    class Kind(models.TextChoices):
        NOTE = "note"
        WARNING = "warning"
        HOLD = "hold"
        SUSPENSION = "suspension"
        READ_ONLY = "read_only"
        BAN = "ban"
        BAN_REVERSAL = "ban_reversal"
        SPONSORSHIP_TRANSFER = "sponsorship_transfer"
        SPONSORING_SUSPENSION = "sponsoring_suspension"

    class Status(models.TextChoices):
        PENDING = "pending"
        ACTIVE = "active"
        EXPIRED = "expired"
        REVERSED = "reversed"

    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="moderation_actions"
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # Null when an Admin or Owner acted alone: the record then shows a single actor.
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    internal_reason = models.TextField()
    public_summary = models.TextField(blank=True)
    is_public = models.BooleanField(default=True)
    related_post = models.ForeignKey(
        "boards.Post", null=True, blank=True, on_delete=models.PROTECT, related_name="moderation_actions"
    )
    related_action = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="follow_ups"
    )

    objects = ModerationActionQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~Q(kind="note") | Q(is_public=False), name="notes_are_private"),
            models.CheckConstraint(
                condition=Q(approved_by__isnull=True) | ~Q(approved_by=models.F("initiated_by")),
                name="approver_differs_from_initiator",
            ),
        ]
        indexes = [models.Index(fields=["target_user", "kind", "status"])]

    def __str__(self):
        return f"{self.kind} on {self.target_user} ({self.status})"


class SponsorReview(models.Model):
    """Opened when a Guest or Provisional is banned, so an Admin or Owner can decide whether
    their sponsor should face consequences. Nothing happens to the sponsor until it is decided."""

    class Status(models.TextChoices):
        PENDING = "pending"
        DECIDED = "decided"

    class Outcome(models.TextChoices):
        NO_ACTION = "no_action"
        WARNING = "warning"
        SPONSORING_SUSPENSION = "sponsoring_suspension"
        BAN = "ban"

    banned_member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    sponsor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sponsor_reviews")
    triggering_action = models.OneToOneField(
        ModerationAction, on_delete=models.PROTECT, related_name="sponsor_review"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    outcome = models.CharField(max_length=24, choices=Outcome.choices, blank=True)
    suspension_months = models.PositiveSmallIntegerField(null=True, blank=True)
    invitees_transfer = models.BooleanField(default=False)
    resulting_action = models.OneToOneField(
        ModerationAction, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class DMAccessGrant(models.Model):
    moderator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dm_grants")
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    subject_users = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+")
    case_note = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
