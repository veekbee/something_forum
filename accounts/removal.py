"""Removal, leaving and reinstatement (docs/DESIGN.md, Removal; rule 66).

Removal ends a membership and is not discipline. An Admin or Owner removes a member with a written
reason, and a member may leave on their own. The account can no longer sign in; its posts stay under
the member's name and its DMs are kept. Any Stripe renewal is cancelled, with no automatic refund.
The member's pre-Tenure sponsees open transfers, the profile shows "membership ended" and the date,
and nothing goes on the Rap Sheet. Removal is not erasure. An Admin or Owner can reinstate a removed
member, restoring the account as it was."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import roles, sessions
from accounts.models import User, UserSession
from audit import log
from core.services import require


def _cancel_renewal(member):
    from billing import stripe_api
    from billing.models import Subscription

    sub = Subscription.objects.select_for_update().filter(user=member).first()
    if sub is not None and sub.stripe_subscription_id:
        stripe_api.cancel_subscription(sub.stripe_subscription_id)
        sub.stripe_subscription_id = ""
        sub.save(update_fields=["stripe_subscription_id"])


def end_membership(member, actor, reason, end_reason, transfer_decision=None):
    """The work removal and leaving share. Must run inside a transaction."""
    from sponsorship import capacity, transfers
    from sponsorship.models import Sponsorship, SponsorshipOffer, SponsorshipTransfer

    now = timezone.now()
    User.objects.filter(pk=member.pk).update(status=User.Status.REMOVED, removed_at=now, removed_by=actor,
                                             removal_reason=reason)
    member.status, member.removed_at, member.removed_by, member.removal_reason = User.Status.REMOVED, now, actor, reason
    old = Sponsorship.objects.select_for_update().filter(member=member, ended_at__isnull=True).first()
    if old is not None:
        old.ended_at, old.end_reason = now, end_reason
        old.save()
    # A waiting sponsee's transfer ends with them.
    for transfer in SponsorshipTransfer.objects.select_for_update().filter(member=member, status="open"):
        transfers._close(transfer, SponsorshipTransfer.Status.DECIDED, now, decided_by=actor,
                         decision=SponsorshipTransfer.Decision.REMOVED, notes=reason)
        if transfer_decision is None:
            log.record(actor, "sponsorship.transfer_decide", transfer, {"decision": "removed", "member": member.pk})
    SponsorshipOffer.objects.filter(offerer=member, status=SponsorshipOffer.Status.OPEN).update(
        status=SponsorshipOffer.Status.WITHDRAWN, decided_at=now)
    transfers.close_requests_from(member, now)
    transfers.close_requests_to(member, now)
    _cancel_renewal(member)
    sessions.end_all(member, UserSession.RevokeReason.SIGNED_OUT)
    if old is not None:
        capacity.reassign_slots(old.sponsor)
    transfers.open_for_sponsor(member, transfers.Cause.SPONSOR_LEFT, actor)
    return old


@transaction.atomic
def remove(actor, member, reason):
    """An Admin or Owner ends a membership, with a written reason, at any time."""
    from sponsorship.models import Sponsorship

    require(actor, "member.remove", member)
    if not (reason or "").strip():
        raise ValidationError("Give the reason, for staff.")
    end_membership(member, actor, reason.strip(), Sponsorship.EndReason.MEMBER_REMOVED)
    log.record(actor, "member.remove", member, {"reason": reason.strip()})
    return member


@transaction.atomic
def leave(member, reason=""):
    """A member ends their own membership from their settings."""
    from sponsorship.models import Sponsorship

    require(member, "member.leave", member)
    end_membership(member, None, (reason or "").strip(), Sponsorship.EndReason.MEMBER_LEFT)
    log.record(member, "member.leave", member)
    return member


def _restore_sponsorship(member, actor):
    """A pre-Tenure member gets their last sponsor back, in a new row pointing at the old one. If
    that sponsor can no longer sponsor, the member waits for a new one."""
    from sponsorship import transfers
    from sponsorship.models import Sponsorship

    if roles.trust_rank(member) >= roles.rank_of(roles.TENURED):
        return None
    last = Sponsorship.objects.filter(
        member=member, end_reason__in=[Sponsorship.EndReason.MEMBER_REMOVED, Sponsorship.EndReason.MEMBER_LEFT],
    ).order_by("-ended_at").first()
    if last is None or Sponsorship.objects.filter(member=member, ended_at__isnull=True).exists():
        return None
    sponsor = last.sponsor
    restored = Sponsorship.objects.create(sponsor=sponsor, member=member, sponsor_role=roles.trust_role(sponsor)
                                          or last.sponsor_role, previous=last, is_original=False)
    if not roles.is_admin_or_owner(sponsor) and not transfers.sponsor_fit(sponsor):
        transfers.open_for_member(restored, transfers.cause_for(sponsor), actor)
    return restored


@transaction.atomic
def reinstate(actor, member, reason):
    """Restore a removed member's account as it was: they can sign in again, their sponsorship is
    restored, and transfers their own leaving opened resume where they can."""
    from sponsorship import transfers

    require(actor, "member.reinstate", member)
    if not (reason or "").strip():
        raise ValidationError("Give the reason, for staff.")
    role = roles.trust_role(member)
    status = User.Status.GUEST if role is not None and role.name == roles.GUEST else User.Status.ACTIVE
    User.objects.filter(pk=member.pk).update(status=status, removed_at=None, removed_by=None, removal_reason="")
    member.status, member.removed_at, member.removed_by, member.removal_reason = status, None, None, ""
    restored = _restore_sponsorship(member, actor)
    transfers.resume_for_sponsor(member, actor)
    log.record(actor, "member.reinstate", member, {"reason": reason.strip(),
                                                   "sponsorship": restored.pk if restored else None})
    return member


@transaction.atomic
def refund(actor, charge):
    """An Admin or Owner refunds a removed member's payment in a particular case."""
    from billing import stripe_api
    from billing.models import Charge

    charge = Charge.objects.select_for_update().select_related("user").get(pk=charge.pk)
    require(actor, "billing.refund_removed", charge)
    stripe_api.refund(charge.stripe_payment_intent_id)
    Charge.objects.filter(pk=charge.pk).update(status="refunded")
    log.record(actor, "billing.refund", charge, {"amount_cents": charge.amount_cents, "member": charge.user_id})
    return charge
