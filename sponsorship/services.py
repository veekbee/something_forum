import hashlib
import secrets

from django.db import transaction

from audit import log
from core.services import require
from sponsorship.models import Invitation


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


@transaction.atomic
def create_invitation(sponsor, invitee_email, vouching_notes):
    """Returns (invitation, token). The token is shown or emailed once and only its hash is kept.
    Inviting is allowed at cap; whether the invitation fits is decided when the invitee accepts
    (capacity.has_slot_for), and one that does not is waitlisted."""
    require(sponsor, "member.sponsor")
    token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        sponsor=sponsor, invitee_email=invitee_email, token_hash=hash_token(token), vouching_notes=vouching_notes
    )
    log.record(sponsor, "invitation.create", invitation, {"invitee_email": invitee_email})
    return invitation, token
