"""Sponsorship slots (design rule 16). Slots go first to the sponsor's active sponsorships, then to
live invitations (pending, accepted, waitlisted) in the order they were sent. Inviting is never
refused for want of a slot: an invitee who accepts without one is waitlisted."""

from accounts import roles
from audit import log
from core import registry
from sponsorship.models import Invitation, Sponsorship


def cap_for(sponsor):
    """The sponsor's cap from their trust role, or None for unlimited (Admins, Owners)."""
    role = roles.trust_role(sponsor)
    if role is None:
        return 0
    if role.name in (roles.ADMIN, roles.OWNER):
        return None
    return registry.site_value(f"sponsorship.cap.{role.name}")


def _active(sponsor):
    return Sponsorship.objects.filter(sponsor=sponsor, ended_at__isnull=True).count()


def _live_in_send_order(sponsor):
    return list(Invitation.objects.live().filter(sponsor=sponsor).order_by("created_at", "pk"))


def slot_holders(sponsor):
    """Primary keys of the sponsor's live invitations that hold a slot."""
    live = _live_in_send_order(sponsor)
    cap = cap_for(sponsor)
    if cap is None:
        return {inv.pk for inv in live}
    free = max(cap - _active(sponsor), 0)
    return {inv.pk for inv in live[:free]}


def has_slot_for(invitation):
    return invitation.pk in slot_holders(invitation.sponsor)


def at_capacity(sponsor):
    """True when a new invitation would not hold a slot, so its invitee would be waitlisted."""
    cap = cap_for(sponsor)
    return cap is not None and _active(sponsor) + Invitation.objects.live().filter(sponsor=sponsor).count() >= cap


def reassign_slots(sponsor):
    """After a slot frees: a waitlisted invitation that now holds one becomes accepted. Must run
    inside the transaction that freed the slot. Returns the invitations that moved."""
    holders = slot_holders(sponsor)
    moved = []
    for invitation in Invitation.objects.select_for_update().filter(
        pk__in=holders, status=Invitation.Status.WAITLISTED
    ):
        invitation.status = Invitation.Status.ACCEPTED
        invitation.save(update_fields=["status"])
        log.record(None, "invitation.slot_assigned", invitation, {"sponsor": sponsor.pk})
        moved.append(invitation)
    return moved


def reassign_all():
    """For a change to the caps themselves: every sponsor with someone waiting."""
    from accounts.models import User

    waiting = User.objects.filter(invitations_sent__status=Invitation.Status.WAITLISTED).distinct()
    return [inv for sponsor in waiting for inv in reassign_slots(sponsor)]
