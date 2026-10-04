from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.immutability import HistoryRowMixin


class InvitationQuerySet(models.QuerySet):
    def live(self):
        """Invitations still in play: they hold or wait for a sponsorship slot."""
        return self.filter(status__in=Invitation.LIVE)


class Invitation(models.Model):
    """See the onboarding table in docs/DESIGN.md. Accepting creates the invitee's account (status
    invited); approval makes them a Guest and records the first Sponsorship."""

    class Status(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        WAITLISTED = "waitlisted"
        APPROVED = "approved"
        DECLINED = "declined"
        INVITEE_DECLINED = "invitee_declined"
        RESCINDED = "rescinded"
        EXPIRED = "expired"

    LIVE = (Status.PENDING, Status.ACCEPTED, Status.WAITLISTED)
    # Endings without approval; the invited account, if any, can no longer sign in.
    ENDED = (Status.DECLINED, Status.INVITEE_DECLINED, Status.RESCINDED, Status.EXPIRED)

    sponsor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invitations_sent")
    invitee_email = models.EmailField()
    # Only a hash is stored; the token itself goes to the invitee by email.
    token_hash = models.CharField(max_length=64, unique=True)
    vouching_notes = models.TextField()
    # SET_NULL because an invited account whose invitation ends unapproved is deleted later
    # (design rule 19); the Invitation row stays as history.
    invitee = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="invitation"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    objects = InvitationQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["sponsor", "created_at"])]


class Sponsorship(HistoryRowMixin, models.Model):
    """A sponsor vouching for a member. Active while ended_at is null. The pedigree is the set of
    is_original rows; a transfer closes one row and opens another with `previous` pointing back."""

    class EndReason(models.TextChoices):
        TENURED = "tenured"
        TRANSFERRED = "transferred"
        SPONSOR_LEFT = "sponsor_left"
        SPONSOR_BANNED = "sponsor_banned"
        MEMBER_REMOVED = "member_removed"
        MEMBER_LEFT = "member_left"

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


class SponsorshipTransfer(models.Model):
    """A pre-Tenure member who needs a new sponsor (docs/DESIGN.md, Sponsorship transfer; rules 51
    to 53). The old Sponsorship row stays active while this is open, so resuming needs no new row."""

    class Cause(models.TextChoices):
        SPONSOR_LEFT = "sponsor_left"
        SPONSOR_BANNED = "sponsor_banned"
        SPONSOR_ROLE_LOST = "sponsor_role_lost"
        SPONSOR_LAPSE_RESTRICTED = "sponsor_lapse_restricted"
        SPONSOR_REVIEW = "sponsor_review"

    class Status(models.TextChoices):
        OPEN = "open"
        COMPLETED = "completed"
        RESUMED = "resumed"
        DECIDED = "decided"

    class Decision(models.TextChoices):
        ADMIN_SPONSORED = "admin_sponsored"
        EXTENDED = "extended"
        REMOVED = "removed"

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sponsorship_transfers")
    sponsorship = models.ForeignKey(Sponsorship, on_delete=models.PROTECT, related_name="transfers")
    cause = models.CharField(max_length=24, choices=Cause.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    started_at = models.DateTimeField(default=timezone.now)
    deadline_at = models.DateTimeField()
    # When the wait ended, however it ended: the Provisional clock is paused until then.
    ended_at = models.DateTimeField(null=True, blank=True)
    new_sponsorship = models.OneToOneField(
        Sponsorship, null=True, blank=True, on_delete=models.PROTECT, related_name="transfer_into"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decision = models.CharField(max_length=16, choices=Decision.choices, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["member"], condition=Q(status="open"), name="one_open_transfer_per_member")
        ]

    @property
    def past_deadline(self):
        return self.status == self.Status.OPEN and self.deadline_at <= timezone.now()


class VouchRequest(models.Model):
    """A waiting sponsee asks a member who could sponsor them to vouch (rule 67). A notification,
    never a DM; the recipient offers or ignores it. Closes when the transfer ends."""

    class Status(models.TextChoices):
        OPEN = "open"
        ANSWERED = "answered"
        IGNORED = "ignored"
        WITHDRAWN = "withdrawn"

    transfer = models.ForeignKey(SponsorshipTransfer, on_delete=models.PROTECT, related_name="vouch_requests")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vouch_requests")
    note = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transfer", "recipient"], condition=Q(status="open"), name="one_open_request_per_recipient"
            )
        ]


class SponsorshipOffer(models.Model):
    """A member offering to vouch for someone whose transfer is open."""

    class Status(models.TextChoices):
        OPEN = "open"
        ACCEPTED = "accepted"
        DECLINED = "declined"
        WITHDRAWN = "withdrawn"
        LAPSED = "lapsed"

    transfer = models.ForeignKey(SponsorshipTransfer, on_delete=models.PROTECT, related_name="offers")
    offerer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sponsorship_offers")
    vouching_notes = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transfer", "offerer"], condition=Q(status="open"), name="one_open_offer_per_offerer"
            )
        ]


class PromotionQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status__in=[Promotion.Status.RECOMMENDED, Promotion.Status.REVIEWED])


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

    objects = PromotionQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["member"],
                condition=Q(status__in=["recommended", "reviewed"]),
                name="one_open_promotion_per_member",
            )
        ]
