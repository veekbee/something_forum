"""Sponsorship caps. Active sponsorships and pending invitations hold slots; an invitation beyond
the cap is not refused but waitlisted when its invitee accepts (docs/DESIGN.md, Sponsorship)."""

from accounts import roles
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


def slots_used(sponsor):
    active = Sponsorship.objects.filter(sponsor=sponsor, ended_at__isnull=True).count()
    pending = Invitation.objects.filter(sponsor=sponsor, status=Invitation.Status.PENDING).count()
    return active + pending


def at_capacity(sponsor):
    cap = cap_for(sponsor)
    return cap is not None and slots_used(sponsor) >= cap


def has_slot_for(invitation):
    """Whether this invitation fits under the cap. Pending invitations hold slots in the order
    they were sent, so an earlier invitation is never pushed out by a later one."""
    cap = cap_for(invitation.sponsor)
    if cap is None:
        return True
    active = Sponsorship.objects.filter(sponsor=invitation.sponsor, ended_at__isnull=True).count()
    earlier = Invitation.objects.filter(
        sponsor=invitation.sponsor, status=Invitation.Status.PENDING, pk__lt=invitation.pk
    ).count()
    return active + earlier < cap
