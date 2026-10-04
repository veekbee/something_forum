"""Moderation workflow. A Moderator's action is initiated by one person and approved by a second;
an Admin or Owner may do both alone, and a staff note needs no approver (design rule 2). A pending
action may be declined by an eligible approver or withdrawn by its initiator (rule 36). Effects and
audit entries are written in the same transaction as the status change.

A ban never changes the account's status: a member is banned while a ban is in force, so lifting it
restores their previous status and role by construction."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import roles
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
    related_action=None, scope_subforums=(),
):
    """`public_summary` is the initiator's draft; the approver may edit it before approving."""
    scope_subforums = tuple(scope_subforums)
    require(actor, "moderation.initiate", ActionRequest(kind, target_user, related_post, scope_subforums))
    if not internal_reason.strip():
        raise ValidationError("Give the reason, for staff.")
    action = ModerationAction.objects.create(
        target_user=target_user,
        kind=kind,
        initiated_by=actor,
        ends_at=ends_at,
        internal_reason=internal_reason,
        public_summary_draft=public_summary,
        is_public=kind != Kind.NOTE,
        related_post=related_post,
        related_action=related_action,
    )
    action.scope_subforums.set(scope_subforums)
    log.record(actor, "moderation.initiate", action, {
        "kind": kind, "target_user": target_user.pk, "scope_subforums": [sf.pk for sf in scope_subforums],
    })
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


def _locked(action):
    return ModerationAction.objects.select_for_update(of=("self",)).select_related(
        "target_user", "related_post__thread__subforum"
    ).get(pk=action.pk)


@transaction.atomic
def approve_action(actor, action, public_summary=None):
    """The approver may edit the public summary; the initiator's draft is kept beside it."""
    action = _locked(action)
    require(actor, "moderation.approve", action)
    action.approved_by = actor
    if public_summary is not None:
        action.public_summary = public_summary
    action.save(update_fields=["approved_by", "public_summary"])
    _activate(actor, action)
    return action


@transaction.atomic
def decline_action(actor, action, reason):
    action = _locked(action)
    require(actor, "moderation.decline", action)
    if not reason.strip():
        raise ValidationError("Say why, for the initiator.")
    action.status, action.declined_by, action.decline_reason = ModerationAction.Status.DECLINED, actor, reason.strip()
    action.save(update_fields=["status", "declined_by", "decline_reason"])
    log.record(actor, "moderation.decline", action, {"kind": action.kind, "reason": action.decline_reason})
    Notification.objects.create(recipient_id=action.initiated_by_id, kind="moderation.declined",
                                payload={"action": action.pk, "reason": action.decline_reason})
    return action


@transaction.atomic
def withdraw_action(actor, action):
    action = _locked(action)
    require(actor, "moderation.withdraw", action)
    action.status = ModerationAction.Status.WITHDRAWN
    action.save(update_fields=["status"])
    log.record(actor, "moderation.withdraw", action, {"kind": action.kind})
    return action


@transaction.atomic
def lift_ban(actor, ban, internal_reason):
    """An Admin or Owner lifts a ban. The record keeps the ban, marked lifted with the date, without
    saying how; the reversal is recorded as a private follow-up action."""
    ban = _locked(ban)
    require(actor, "moderation.lift_ban", ban)
    now = timezone.now()
    ban.status, ban.ends_at = ModerationAction.Status.REVERSED, now
    ban.save(update_fields=["status", "ends_at"])
    ModerationAction.objects.create(
        target_user=ban.target_user, kind=Kind.BAN_REVERSAL, initiated_by=actor, status=ModerationAction.Status.ACTIVE,
        starts_at=now, internal_reason=internal_reason or "Ban lifted", is_public=False, related_action=ban,
    )
    log.record(actor, "moderation.lift_ban", ban, {"target_user": ban.target_user_id})
    Notification.objects.create(recipient_id=ban.target_user_id, kind="moderation.ban_lifted", payload={"action": ban.pk})
    return ban


def expire_actions(now=None):
    """Mark time-limited actions past their end as expired, so the record reads correctly. They have
    already stopped applying: permissions check ends_at themselves. Returns how many changed."""
    now = now or timezone.now()
    count = 0
    for pk in ModerationAction.objects.filter(
        status=ModerationAction.Status.ACTIVE, ends_at__isnull=False, ends_at__lte=now
    ).values_list("pk", flat=True):
        with transaction.atomic():
            action = ModerationAction.objects.select_for_update().get(pk=pk)
            if action.status != ModerationAction.Status.ACTIVE:
                continue
            action.status = ModerationAction.Status.EXPIRED
            action.save(update_fields=["status"])
            log.record(None, "moderation.expire", action, {"kind": action.kind})
            count += 1
    return count


def _activate(actor, action):
    action.status = ModerationAction.Status.ACTIVE
    action.starts_at = timezone.now()
    if not action.public_summary:
        action.public_summary = action.public_summary_draft
    action.save(update_fields=["status", "starts_at", "public_summary"])
    log.record(actor, "moderation.activate", action, {"kind": action.kind, "single_actor": action.approved_by_id is None})
    if action.kind != Kind.NOTE:
        # Actions taken on a member's account are always reported to them (docs/DESIGN.md, Notifications).
        Notification.objects.create(recipient_id=action.target_user_id, kind="moderation.action",
                                    payload={"action": action.pk, "kind": action.kind})
    if action.kind == Kind.BAN:
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
