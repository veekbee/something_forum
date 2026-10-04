from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.fields import EncryptedTextField


REASONS = {
    "off_topic": "Off-topic",
    "personal_attack": "Personal attack",
    "spam": "Spam",
    "private_information": "Private information",
    "other": "Other",
}


def reason_text(key, note=""):
    """The preset reason in words; "other" must come with a sentence."""
    if key not in REASONS:
        raise ValidationError("Choose a reason from the list.")
    note = note.strip()
    if key == "other" and not note:
        raise ValidationError("Say in a sentence why.")
    return f"{REASONS[key]}: {note}" if note else REASONS[key]


class ReportQuerySet(models.QuerySet):
    def waiting(self):
        return self.filter(status__in=[Report.Status.OPEN, Report.Status.ESCALATED])


class Report(models.Model):
    """Member reports and automatic flags in one table, so the moderation queue reads one list."""

    class Source(models.TextChoices):
        MEMBER = "member"
        SYSTEM = "system"

    class Kind(models.TextChoices):
        POST = "post"
        MEMBER = "member"
        DM = "dm"
        # A Moderator escalating a queue item that is not itself a report: a held post, a pending
        # action or a promotion awaiting review. Created already escalated (decided 3 Oct 2026).
        ESCALATION = "escalation"
        FLAG_RATE_LIMIT = "flag_rate_limit"
        FLAG_RAPID_DELETION = "flag_rapid_deletion"
        FLAG_REQUEST_RATE = "flag_request_rate"

    class Status(models.TextChoices):
        OPEN = "open"
        ESCALATED = "escalated"
        RESOLVED = "resolved"

    class Outcome(models.TextChoices):
        ACTION_TAKEN = "action_taken"
        NO_ACTION = "no_action"

    source = models.CharField(max_length=8, choices=Source.choices, default=Source.MEMBER)
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.POST)
    post = models.ForeignKey("boards.Post", null=True, blank=True, on_delete=models.PROTECT, related_name="reports")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="reports_against"
    )
    related_action = models.ForeignKey(
        "moderation.ModerationAction", null=True, blank=True, on_delete=models.PROTECT, related_name="escalations"
    )
    related_promotion = models.ForeignKey(
        "sponsorship.Promotion", null=True, blank=True, on_delete=models.PROTECT, related_name="escalations"
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="reports_made"
    )
    reason = models.CharField(max_length=32, blank=True, choices=[(k, v) for k, v in REASONS.items()])
    note = models.TextField(blank=True)
    # Flag context (counts, sub-forum) and staff notes added while it waits.
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    escalated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    escalation_note = models.TextField(blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    objects = ReportQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(post__isnull=False) | Q(user__isnull=False), name="report_has_subject"),
            models.CheckConstraint(
                condition=Q(source="system") | Q(reporter__isnull=False), name="member_reports_have_reporter"
            ),
        ]
        indexes = [models.Index(fields=["status", "created_at"])]

    @property
    def is_flag(self):
        return self.source == self.Source.SYSTEM


class ModerationActionQuerySet(models.QuerySet):
    def in_force(self, at=None):
        """Active actions whose period covers `at` (default now), whatever the expiry job has done."""
        at = at or timezone.now()
        return self.filter(status=ModerationAction.Status.ACTIVE, starts_at__lte=at).filter(
            Q(ends_at__isnull=True) | Q(ends_at__gt=at)
        )

    def sitewide(self):
        return self.filter(scope_subforums__isnull=True)

    def applying_in(self, subforum):
        """Site-wide actions and those whose limits include `subforum`."""
        return self.filter(Q(scope_subforums__isnull=True) | Q(scope_subforums=subforum)).distinct()


class ModerationAction(models.Model):
    class Kind(models.TextChoices):
        NOTE = "note"
        WARNING = "warning"
        HOLD = "hold"
        SUSPENSION = "suspension"
        # Site-wide read-only as a disciplinary status. Called read_only before 3 Oct 2026.
        PROBATION = "probation"
        BAN = "ban"
        # Applies to the person, not the account, and cannot be bought back. Owners only.
        PERMANENT_BAN = "permanent_ban"
        BAN_REVERSAL = "ban_reversal"
        SPONSORSHIP_TRANSFER = "sponsorship_transfer"
        SPONSORING_SUSPENSION = "sponsoring_suspension"

    class Status(models.TextChoices):
        PENDING = "pending"
        ACTIVE = "active"
        EXPIRED = "expired"
        REVERSED = "reversed"
        DECLINED = "declined"
        WITHDRAWN = "withdrawn"
        # A Permanent Ban an Owner annulled to correct an error.
        ANNULLED = "annulled"

    # Statuses that appear on the public record; declined and withdrawn actions never do.
    RECORD_STATUSES = (Status.ACTIVE, Status.EXPIRED, Status.REVERSED, Status.ANNULLED)

    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="moderation_actions"
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    # Set only for a suspension or hold limited to some sub-forums; empty means site-wide (rule 36).
    scope_subforums = models.ManyToManyField("boards.SubForum", blank=True, related_name="+")
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # Null when an Admin or Owner acted alone: the record then shows a single actor.
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    declined_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decline_reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    internal_reason = models.TextField()
    # The initiator drafts the public summary; the approver may edit it; both are kept.
    public_summary_draft = models.TextField(blank=True)
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


class PermanentBanRecord(models.Model):
    """The permanent-ban list (rule 46): a Permanently Banned person's verified email addresses and
    real name. Visible only to Admins and Owners, checked on every invitation, kept through erasure."""

    action = models.OneToOneField(ModerationAction, on_delete=models.PROTECT, related_name="permanent_ban_record")
    # A JSON list of addresses, encrypted as one value.
    emails = EncryptedTextField(blank=True)
    real_name = EncryptedTextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    annulled_at = models.DateTimeField(null=True, blank=True)
