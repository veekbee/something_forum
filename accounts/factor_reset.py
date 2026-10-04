"""Resetting a lost second factor (docs/DESIGN.md, Authentication; rule 63).

An Admin or Owner resets it from the per-member view after confirming the member's identity, and
records how: the sponsor was consulted, or, when the sponsor is staff or gone, another way, with a
note. The reset signs out every session, removes the member's authenticator and recovery codes, so
the next sign-in sets up a new one, and sends an account email. An Admin's reset needs an Owner, and
an Owner's another Owner. The reset_second_factor command on the server, which needs access to the
hosting account, can reset any Owner, so the forum is never left without a way back in. Every reset
is audited."""

from allauth.mfa.models import Authenticator
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts import roles, sessions
from accounts.models import User, UserSession
from audit import log
from core.models import Notification
from core.services import require

SPONSOR, OTHER, SERVER = "sponsor_consulted", "other", "server_command"
METHODS = {SPONSOR: "The sponsor was consulted", OTHER: "Another way (the sponsor is staff or gone)"}


def sponsor_unavailable(member):
    """True when there is no sponsor to consult: none active (Tenured and above, for example), or
    the sponsor is staff, or their account can no longer be reached."""
    from boards import dm

    sponsor = dm.active_sponsor(member)
    if sponsor is None:
        return True
    if dm.is_staff_role(sponsor) or sponsor.status in (User.Status.REMOVED, User.Status.TOMBSTONE):
        return True
    from moderation.permanent import is_permanently_banned

    return is_permanently_banned(sponsor)


def _reset(member, actor, method, note):
    Authenticator.objects.filter(user=member).delete()
    sessions.end_all(member, UserSession.RevokeReason.FACTOR_RESET)
    log.record(actor, "account.factor_reset", member, {"method": method, "note": note})
    Notification.objects.create(recipient=member, kind="account.factor_reset", payload={})


@transaction.atomic
def reset(actor, member, method, note=""):
    require(actor, "account.reset_factor", member)
    note = note.strip()
    if method not in METHODS:
        raise ValidationError("Say how you confirmed who they are.")
    if method == OTHER:
        if not sponsor_unavailable(member):
            raise ValidationError("Consult their sponsor; another way is only for when the sponsor is staff or gone.")
        if not note:
            raise ValidationError("Note how you confirmed who they are.")
    _reset(member, actor, method, note)


@transaction.atomic
def reset_owner(email, note):
    """The server command: resets an Owner's second factor, for someone with access to the hosting
    account (rule 63)."""
    owner = User.objects.filter(email__iexact=(email or "").strip()).first()
    if owner is None or not roles.is_owner(owner):
        raise ValidationError("This command resets only an Owner's second factor.")
    if not note.strip():
        raise ValidationError("Note why, for the audit log.")
    _reset(owner, None, SERVER, note.strip())
    return owner
