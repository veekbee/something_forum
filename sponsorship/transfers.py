"""Sponsorship transfer (docs/DESIGN.md, Sponsorship transfer; rules 51 to 53).

When a sponsor leaves, is banned, loses the role that allows sponsoring, reaches the restriction
stage of a lapse, or a sponsor review orders it, each of their pre-Tenure sponsees needs a new
sponsor. Until one is accepted the sponsee is read-only and their Provisional clock pauses. Willing
members offer to vouch and the sponsee accepts; an Admin or Owner may step in as sponsor. Past the
deadline the transfer waits in the moderation queue; nothing happens to the member automatically.

The old Sponsorship row stays active while a transfer is open, so if the cause ends first the
original sponsorship simply resumes. Transfers caused by a sponsor review or a Permanent Ban never
resume, and every function here must run inside the transaction of the change that caused it."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts import roles
from accounts.models import User
from audit import log
from core import registry
from core.models import Notification
from core.services import require
from sponsorship import capacity
from sponsorship.models import Sponsorship, SponsorshipOffer, SponsorshipTransfer, VouchRequest

Cause = SponsorshipTransfer.Cause
Status = SponsorshipTransfer.Status

CLOSED_STATUSES = (User.Status.INVITED, User.Status.REMOVED, User.Status.TOMBSTONE)


def open_transfer(member):
    return SponsorshipTransfer.objects.filter(member=member, status=Status.OPEN).first()


def awaiting_sponsor(member):
    return SponsorshipTransfer.objects.filter(member=member, status=Status.OPEN).exists()


def _notify(user, kind, transfer, **extra):
    Notification.objects.create(recipient=user, kind=kind, payload={"transfer": transfer.pk, **extra})


# --- opening -------------------------------------------------------------------------------


def open_for_member(sponsorship, cause, actor=None):
    """Open one transfer, for a reinstated member whose restored sponsor can no longer sponsor."""
    now = timezone.now()
    transfer = SponsorshipTransfer.objects.create(
        member=sponsorship.member, sponsorship=sponsorship, cause=cause, started_at=now,
        deadline_at=now + timedelta(days=registry.site_value("sponsorship.transfer_grace_days")),
    )
    log.record(actor, "sponsorship.transfer_open", transfer, {
        "member": sponsorship.member_id, "sponsor": sponsorship.sponsor_id, "cause": cause,
    })
    _notify(sponsorship.member, "sponsorship.transfer_opened", transfer)
    return transfer


def cause_for(sponsor):
    """Which cause keeps this sponsor from sponsoring now."""
    from billing.lapse import is_restricted
    from moderation.models import ModerationAction

    if sponsor.status in CLOSED_STATUSES:
        return Cause.SPONSOR_LEFT
    if ModerationAction.objects.in_force().filter(
        target_user=sponsor, kind__in=[ModerationAction.Kind.BAN, ModerationAction.Kind.PERMANENT_BAN]
    ).exists():
        return Cause.SPONSOR_BANNED
    if is_restricted(sponsor):
        return Cause.SPONSOR_LAPSE_RESTRICTED
    return Cause.SPONSOR_ROLE_LOST


def open_for_sponsor(sponsor, cause, actor=None):
    """Open a transfer for each of the sponsor's current sponsees (all pre-Tenure: a sponsorship
    ends at Tenure). Admin and Owner sponsors never trigger one. A sponsee already waiting keeps
    their open transfer; a final cause makes it final by itself (see resumable)."""
    if roles.is_admin_or_owner(sponsor):
        return []
    now = timezone.now()
    deadline = now + timedelta(days=registry.site_value("sponsorship.transfer_grace_days"))
    opened = []
    for sponsorship in Sponsorship.objects.filter(sponsor=sponsor, ended_at__isnull=True).select_related("member"):
        member = sponsorship.member
        if member.status in CLOSED_STATUSES or awaiting_sponsor(member):
            continue
        transfer = SponsorshipTransfer.objects.create(
            member=member, sponsorship=sponsorship, cause=cause, started_at=now, deadline_at=deadline
        )
        log.record(actor, "sponsorship.transfer_open", transfer, {
            "member": member.pk, "sponsor": sponsor.pk, "cause": cause,
        })
        _notify(member, "sponsorship.transfer_opened", transfer)
        opened.append(transfer)
    return opened


def sponsor_lost_role(sponsor, actor=None):
    """After a role is revoked: if the sponsor can no longer sponsor at all, their sponsees transfer.
    A cap that merely shrinks leaves existing sponsees where they are."""
    if roles.trust_rank(sponsor) >= roles.rank_of(roles.FULL):
        return []
    return open_for_sponsor(sponsor, Cause.SPONSOR_ROLE_LOST, actor)


# --- whether the cause still holds ---------------------------------------------------------


def _permanently_banned_since(sponsor, since):
    """A Permanent Ban in force at any moment from `since` on, even if later annulled."""
    from moderation.models import ModerationAction

    return ModerationAction.objects.filter(
        target_user=sponsor, kind=ModerationAction.Kind.PERMANENT_BAN, starts_at__isnull=False,
        status__in=[ModerationAction.Status.ACTIVE, ModerationAction.Status.ANNULLED],
    ).filter(Q(ends_at__isnull=True) | Q(ends_at__gte=since)).exists()


def resumable(transfer):
    """Transfers ordered by a sponsor review, or caused by a Permanent Ban, are always final."""
    from moderation.models import SponsorReview

    if transfer.cause == Cause.SPONSOR_REVIEW:
        return False
    sponsor = transfer.sponsorship.sponsor
    if _permanently_banned_since(sponsor, transfer.started_at):
        return False
    return not SponsorReview.objects.filter(
        sponsor=sponsor, invitees_transfer=True, decided_at__gte=transfer.started_at
    ).exists()


def sponsor_fit(sponsor):
    """None of the transfer causes applies to the sponsor any more."""
    from billing.lapse import is_restricted
    from moderation.models import ModerationAction

    if sponsor.status in CLOSED_STATUSES:
        return False
    if roles.trust_rank(sponsor) < roles.rank_of(roles.FULL):
        return False
    if ModerationAction.objects.in_force().filter(
        target_user=sponsor, kind__in=[ModerationAction.Kind.BAN, ModerationAction.Kind.PERMANENT_BAN]
    ).exists():
        return False
    return not is_restricted(sponsor)


def _close(transfer, status, now, **fields):
    transfer.status, transfer.ended_at = status, now
    for name, value in fields.items():
        setattr(transfer, name, value)
    transfer.save()
    SponsorshipOffer.objects.filter(transfer=transfer, status=SponsorshipOffer.Status.OPEN).update(
        status=SponsorshipOffer.Status.LAPSED, decided_at=now
    )
    # Requests to vouch close when the transfer ends: the sponsee no longer needs them.
    VouchRequest.objects.filter(transfer=transfer, status=VouchRequest.Status.OPEN).update(
        status=VouchRequest.Status.WITHDRAWN, closed_at=now
    )


def resume_for_sponsor(sponsor, actor=None):
    """Call after anything that may end a cause (a payment, a lifted or expired ban, a role granted):
    open transfers from this sponsor resume where the cause has ended and the transfer may resume."""
    transfers = SponsorshipTransfer.objects.select_for_update().filter(
        sponsorship__sponsor=sponsor, status=Status.OPEN
    ).select_related("sponsorship__sponsor", "member")
    resumed = []
    if not transfers or not sponsor_fit(sponsor):
        return resumed
    now = timezone.now()
    for transfer in transfers:
        if not resumable(transfer):
            continue
        _close(transfer, Status.RESUMED, now)
        log.record(actor, "sponsorship.transfer_resume", transfer, {"member": transfer.member_id, "sponsor": sponsor.pk})
        _notify(transfer.member, "sponsorship.transfer_ended", transfer, outcome="resumed")
        resumed.append(transfer)
    return resumed


def resume_all(actor=None):
    """The daily sweep, for causes that end with time (a ban reaching its end)."""
    sponsors = User.objects.filter(
        sponsorships_given__transfers__status=Status.OPEN
    ).distinct()
    resumed = []
    for sponsor in sponsors:
        with transaction.atomic():
            resumed += resume_for_sponsor(sponsor, actor)
    return resumed


# --- offers --------------------------------------------------------------------------------


@transaction.atomic
def make_offer(offerer, member, vouching_notes):
    require(offerer, "sponsorship.offer", member)
    if not vouching_notes.strip():
        raise ValidationError("Say how you know them, and for how long.")
    transfer = SponsorshipTransfer.objects.select_for_update().get(member=member, status=Status.OPEN)
    offer = SponsorshipOffer.objects.create(transfer=transfer, offerer=offerer, vouching_notes=vouching_notes.strip())
    VouchRequest.objects.filter(transfer=transfer, recipient=offerer, status=VouchRequest.Status.OPEN).update(
        status=VouchRequest.Status.ANSWERED, closed_at=timezone.now())
    log.record(offerer, "sponsorship.offer", offer, {"member": member.pk})
    _notify(member, "sponsorship.offer", transfer)
    return offer


def _locked_offer(offer):
    return SponsorshipOffer.objects.select_for_update(of=("self",)).select_related(
        "transfer__member", "transfer__sponsorship__sponsor", "offerer"
    ).get(pk=offer.pk)


@transaction.atomic
def withdraw_offer(offerer, offer):
    offer = _locked_offer(offer)
    require(offerer, "sponsorship.withdraw_offer", offer)
    offer.status, offer.decided_at = SponsorshipOffer.Status.WITHDRAWN, timezone.now()
    offer.save(update_fields=["status", "decided_at"])
    log.record(offerer, "sponsorship.offer_withdraw", offer)
    return offer


@transaction.atomic
def decline_offer(member, offer):
    offer = _locked_offer(offer)
    require(member, "sponsorship.answer_offer", offer)
    offer.status, offer.decided_at = SponsorshipOffer.Status.DECLINED, timezone.now()
    offer.save(update_fields=["status", "decided_at"])
    log.record(member, "sponsorship.offer_decline", offer)
    Notification.objects.create(recipient=offer.offerer, kind="sponsorship.offer_declined", payload={"offer": offer.pk})
    return offer


def _new_sponsorship(transfer, new_sponsor, now):
    old = Sponsorship.objects.select_for_update().get(pk=transfer.sponsorship_id)
    old.ended_at, old.end_reason = now, Sponsorship.EndReason.TRANSFERRED
    old.save()
    created = Sponsorship.objects.create(
        sponsor=new_sponsor, member=transfer.member, sponsor_role=roles.trust_role(new_sponsor),
        started_at=now, previous=old, is_original=False,
    )
    capacity.reassign_slots(old.sponsor)
    return created


@transaction.atomic
def accept_offer(member, offer):
    """No approval follows: accepting completes the transfer and lapses the other offers."""
    offer = _locked_offer(offer)
    require(member, "sponsorship.accept_offer", offer)
    transfer = SponsorshipTransfer.objects.select_for_update().get(pk=offer.transfer_id)
    now = timezone.now()
    offer.status, offer.decided_at = SponsorshipOffer.Status.ACCEPTED, now
    offer.save(update_fields=["status", "decided_at"])
    _close(transfer, Status.COMPLETED, now, new_sponsorship=_new_sponsorship(transfer, offer.offerer, now))
    log.record(member, "sponsorship.transfer_complete", transfer, {
        "member": member.pk, "new_sponsor": offer.offerer_id, "offer": offer.pk,
    })
    Notification.objects.create(recipient=offer.offerer, kind="sponsorship.offer_accepted", payload={"offer": offer.pk})
    return transfer


# --- Admin and Owner decisions -------------------------------------------------------------


def _locked_transfer(transfer):
    return SponsorshipTransfer.objects.select_for_update(of=("self",)).select_related(
        "member", "sponsorship__sponsor"
    ).get(pk=transfer.pk)


@transaction.atomic
def step_in(actor, transfer, notes=""):
    """An Admin or Owner becomes the sponsor directly, at any time while the transfer is open."""
    transfer = _locked_transfer(transfer)
    require(actor, "sponsorship.decide_transfer", transfer)
    now = timezone.now()
    _close(transfer, Status.DECIDED, now, new_sponsorship=_new_sponsorship(transfer, actor, now),
           decided_by=actor, decision=SponsorshipTransfer.Decision.ADMIN_SPONSORED, notes=notes)
    log.record(actor, "sponsorship.transfer_decide", transfer, {"decision": transfer.decision, "member": transfer.member_id})
    _notify(transfer.member, "sponsorship.transfer_ended", transfer, outcome="admin_sponsored")
    return transfer


@transaction.atomic
def extend(actor, transfer, days, notes=""):
    transfer = _locked_transfer(transfer)
    require(actor, "sponsorship.decide_transfer", transfer)
    if not days or days < 1:
        raise ValidationError("Extend by at least one day.")
    transfer.deadline_at = max(transfer.deadline_at, timezone.now()) + timedelta(days=days)
    transfer.decided_by, transfer.decision = actor, SponsorshipTransfer.Decision.EXTENDED
    transfer.notes = notes
    transfer.save()
    log.record(actor, "sponsorship.transfer_decide", transfer, {
        "decision": transfer.decision, "member": transfer.member_id, "deadline_at": transfer.deadline_at.isoformat(),
    })
    return transfer


@transaction.atomic
def remove_member(actor, transfer, notes=""):
    """After the deadline an Admin or Owner may remove the member (rule 53); removal is the general
    one (accounts.removal), recorded on the transfer as the decision."""
    from accounts import removal

    transfer = _locked_transfer(transfer)
    require(actor, "sponsorship.remove_after_transfer", transfer)
    if not notes.strip():
        raise ValidationError("Give the reason, for staff.")
    member = transfer.member
    removal.end_membership(member, actor, notes.strip(), Sponsorship.EndReason.MEMBER_REMOVED, transfer_decision=True)
    transfer.refresh_from_db()
    log.record(actor, "sponsorship.transfer_decide", transfer, {"decision": transfer.decision, "member": member.pk})
    log.record(actor, "member.remove", member, {"reason": notes.strip(), "transfer": transfer.pk})
    return transfer


# --- requests to vouch (rule 67) -----------------------------------------------------------


@transaction.atomic
def request_vouch(member, recipient, note):
    """A waiting sponsee asks a member who could sponsor them. A notification, never a DM."""
    require(member, "sponsorship.request_vouch", recipient)
    if not (note or "").strip():
        raise ValidationError("Add a short note: who you are to them, and why you are asking.")
    transfer = SponsorshipTransfer.objects.select_for_update().get(member=member, status=Status.OPEN)
    request = VouchRequest.objects.create(transfer=transfer, recipient=recipient, note=note.strip())
    log.record(member, "sponsorship.vouch_request", request, {"recipient": recipient.pk})
    Notification.objects.create(recipient=recipient, kind="sponsorship.vouch_request",
                                payload={"request": request.pk, "member": member.slug})
    return request


def _locked_request(request):
    return VouchRequest.objects.select_for_update(of=("self",)).select_related("transfer").get(pk=request.pk)


@transaction.atomic
def withdraw_request(member, request):
    request = _locked_request(request)
    if request.transfer.member_id != member.pk or request.status != VouchRequest.Status.OPEN:
        raise ValidationError("This request is no longer open.")
    request.status, request.closed_at = VouchRequest.Status.WITHDRAWN, timezone.now()
    request.save(update_fields=["status", "closed_at"])
    log.record(member, "sponsorship.vouch_request_withdraw", request)
    return request


@transaction.atomic
def ignore_request(recipient, request):
    request = _locked_request(request)
    if request.recipient_id != recipient.pk or request.status != VouchRequest.Status.OPEN:
        raise ValidationError("This request is no longer open.")
    request.status, request.closed_at = VouchRequest.Status.IGNORED, timezone.now()
    request.save(update_fields=["status", "closed_at"])
    return request


def close_requests_from(member, now):
    VouchRequest.objects.filter(transfer__member=member, status=VouchRequest.Status.OPEN).update(
        status=VouchRequest.Status.WITHDRAWN, closed_at=now)


def close_requests_to(member, now):
    VouchRequest.objects.filter(recipient=member, status=VouchRequest.Status.OPEN).update(
        status=VouchRequest.Status.IGNORED, closed_at=now)


def close_on_tenure(member, actor=None):
    """A sponsee promoted to Tenured needs no sponsor, so a waiting transfer simply closes."""
    transfer = SponsorshipTransfer.objects.select_for_update().filter(member=member, status=Status.OPEN).first()
    if transfer is None:
        return None
    _close(transfer, Status.COMPLETED, timezone.now(), notes="Promoted to Tenured; no sponsor needed.")
    log.record(actor, "sponsorship.transfer_tenured", transfer, {"member": member.pk})
    return transfer


# --- what waits and what pauses ------------------------------------------------------------


def past_deadline(now=None):
    """Open transfers past their deadline: queue items for Admins and Owners (rule 53)."""
    return SponsorshipTransfer.objects.filter(status=Status.OPEN, deadline_at__lte=now or timezone.now())


def waiting_periods(member):
    """(start, end or None) for each transfer wait, for pausing the Provisional clock."""
    return list(SponsorshipTransfer.objects.filter(member=member).values_list("started_at", "ended_at"))
