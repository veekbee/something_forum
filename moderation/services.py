"""Moderation workflow. A Moderator's action is initiated by one person and approved by a second;
an Admin or Owner may do both alone, and a staff note needs no approver (design rule 2). Effects and audit entries are written in the
same transaction as the status change."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import roles
from accounts.models import User
from audit import log
from core import registry
from core.models import Notification
from core.dates import add_months
from core.permissions import ActionRequest
from core.services import require
from moderation.models import DMAccessGrant, ModerationAction, SponsorReview
from sponsorship.models import Sponsorship

Kind = ModerationAction.Kind


@transaction.atomic
def initiate_action(
    actor, target_user, kind, *, internal_reason, public_summary="", ends_at=None, related_post=None,
    related_action=None,
):
    require(actor, "moderation.initiate", ActionRequest(kind, target_user, related_post))
    action = ModerationAction.objects.create(
        target_user=target_user,
        kind=kind,
        initiated_by=actor,
        ends_at=ends_at,
        internal_reason=internal_reason,
        public_summary=public_summary,
        is_public=kind != Kind.NOTE,
        related_post=related_post,
        related_action=related_action,
    )
    log.record(actor, "moderation.initiate", action, {"kind": kind, "target_user": target_user.pk})
    if kind == Kind.NOTE or roles.is_admin_or_owner(actor):
        _activate(actor, action)
    if kind == Kind.NOTE:
        _notify_leadership_of_note(actor, action)
    return action


def _notify_leadership_of_note(actor, note):
    # The Mod feedback feed will also show notes once it is specified (docs/DESIGN.md, Still open).
    payload = {"action": note.pk, "target_user": note.target_user_id, "initiated_by": actor.pk}
    Notification.objects.bulk_create(
        Notification(recipient=user, kind="moderation.note", payload=payload)
        for user in roles.leadership().exclude(pk=actor.pk)
    )


@transaction.atomic
def approve_action(actor, action):
    require(actor, "moderation.approve", action)
    action.approved_by = actor
    action.save(update_fields=["approved_by"])
    _activate(actor, action)
    return action


def _activate(actor, action):
    action.status = ModerationAction.Status.ACTIVE
    action.starts_at = timezone.now()
    action.save(update_fields=["status", "starts_at"])
    log.record(actor, "moderation.activate", action, {"kind": action.kind, "single_actor": action.approved_by_id is None})
    if action.kind == Kind.BAN:
        User.objects.filter(pk=action.target_user_id).update(status=User.Status.BANNED)
        _open_sponsor_review(actor, action)


def _open_sponsor_review(actor, ban):
    """A banned Guest or Provisional puts their sponsor's conduct in front of an Admin or Owner.
    Nothing happens to the sponsor until the review is decided."""
    if not registry.site_value("sponsorship.review_on_member_ban"):
        return None
    role = roles.trust_role(ban.target_user)
    if role is None or role.name not in (roles.GUEST, roles.PROVISIONAL):
        return None
    sponsorship = Sponsorship.objects.filter(member=ban.target_user, ended_at__isnull=True).first()
    if sponsorship is None or roles.is_admin_or_owner(sponsorship.sponsor):
        return None
    review = SponsorReview.objects.create(
        banned_member=ban.target_user, sponsor=sponsorship.sponsor, triggering_action=ban
    )
    log.record(actor, "sponsor_review.open", review, {"sponsor": sponsorship.sponsor_id, "ban": ban.pk})
    return review


@transaction.atomic
def decide_sponsor_review(actor, review, outcome, *, notes="", public_summary="", months=None, invitees_transfer=False):
    """Record the Admin's or Owner's decision. A suspension of sponsoring privileges lasts
    `months` and the reviewer decides whether the sponsor's pre-Tenure invitees must transfer."""
    require(actor, "sponsor_review.decide", review)
    Outcome = SponsorReview.Outcome
    if outcome == Outcome.SPONSORING_SUSPENSION:
        if not months or months < 1:
            raise ValidationError("a sponsoring suspension needs a number of months")
    elif months is not None or invitees_transfer:
        raise ValidationError("months and invitee transfer apply only to a sponsoring suspension")

    resulting = None
    if outcome != Outcome.NO_ACTION:
        now = timezone.now()
        resulting = initiate_action(
            actor,
            review.sponsor,
            {Outcome.WARNING: Kind.WARNING, Outcome.SPONSORING_SUSPENSION: Kind.SPONSORING_SUSPENSION,
             Outcome.BAN: Kind.BAN}[outcome],
            internal_reason=notes or f"Sponsor review {review.pk}",
            public_summary=public_summary,
            ends_at=add_months(now, months) if months else None,
            related_action=review.triggering_action,
        )
    review.status = SponsorReview.Status.DECIDED
    review.outcome = outcome
    review.suspension_months = months
    review.invitees_transfer = invitees_transfer
    review.resulting_action = resulting
    review.decided_by = actor
    review.decided_at = timezone.now()
    review.notes = notes
    review.save()
    log.record(
        actor, "sponsor_review.decide", review,
        {"outcome": outcome, "months": months, "invitees_transfer": invitees_transfer},
    )
    return review


@transaction.atomic
def grant_dm_access(actor, moderator, subject_users, *, case_note, expires_at):
    require(actor, "dm.grant_access", moderator)
    grant = DMAccessGrant.objects.create(
        moderator=moderator, granted_by=actor, case_note=case_note, expires_at=expires_at
    )
    grant.subject_users.set(subject_users)
    log.record(
        actor, "dm_grant.create", grant,
        {"moderator": moderator.pk, "subjects": [u.pk for u in subject_users], "expires_at": expires_at.isoformat()},
    )
    return grant
