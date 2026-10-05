"""Onboarding transitions, one function per row of the onboarding table in docs/DESIGN.md.

Each runs in a transaction and writes its AuditEntry there. Steps an invitee takes before approval
are recorded with no actor and the invitee's id in the payload: an invited account whose
invitation ends unapproved is deleted later (design rule 19), and audit rows can never be changed
to drop a reference to it.
"""

import secrets
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from accounts import roles
from accounts.models import IdentityRecord, Role, RoleAssignment, User
from audit import log
from billing.models import Subscription
from core import registry
from core.models import Notification
from core.services import require
from sponsorship import capacity
from sponsorship.models import Invitation, Sponsorship
from sponsorship.services import hash_token

Status = Invitation.Status


def _notify_sponsor(invitation, kind):
    Notification.objects.create(
        recipient_id=invitation.sponsor_id,
        kind=kind,
        payload={"invitation": invitation.pk},
    )


def _locked(invitation):
    return Invitation.objects.select_for_update(of=("self",)).select_related("sponsor", "invitee").get(pk=invitation.pk)


def _end(invitation, status, decided_by):
    """End an invitation without approval: free its slot and stop the invited account signing in."""
    invitation.status, invitation.decided_by, invitation.decided_at = status, decided_by, timezone.now()
    invitation.save(update_fields=["status", "decided_by", "decided_at"])
    if invitation.invitee_id:
        User.objects.filter(pk=invitation.invitee_id, status=User.Status.INVITED).update(status=User.Status.REMOVED)
    capacity.reassign_slots(invitation.sponsor)


def expires_at(invitation):
    return invitation.created_at + timedelta(days=registry.site_value("invitation.expiry_days"))


# --- sponsor -------------------------------------------------------------------------------


@transaction.atomic
def send_invitation(sponsor, invitee_email, vouching_notes, accept_url):
    """Create the invitation and email the link. `accept_url(token)` builds the absolute link.
    Returns (invitation, holds_slot): an invitation without a slot is still sent, and its invitee
    is waitlisted on accepting."""
    require(sponsor, "invitation.send")
    email = invitee_email.strip().lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("That address already belongs to an account.")
    if Invitation.objects.live().filter(invitee_email__iexact=email).exists():
        raise ValidationError("That address already has an invitation in progress.")
    if Invitation.objects.filter(invitee_email__iexact=email, status=Status.DECLINED).exists():
        # Whether a declined person can be invited again is still open in the design.
        raise ValidationError("An invitation to that address was declined by an Admin.")
    token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        sponsor=sponsor, invitee_email=email, token_hash=hash_token(token), vouching_notes=vouching_notes
    )
    holds_slot = capacity.has_slot_for(invitation)
    log.record(sponsor, "invitation.send", invitation, {"holds_slot": holds_slot})
    context = {"sponsor": sponsor, "url": accept_url(token), "expiry_days": registry.site_value("invitation.expiry_days")}
    transaction.on_commit(
        lambda: send_mail(
            render_to_string("sponsorship/email/invitation_subject.txt", context).strip(),
            render_to_string("sponsorship/email/invitation_body.txt", context),
            settings.DEFAULT_FROM_EMAIL,
            [email],
        )
    )
    return invitation, holds_slot


@transaction.atomic
def rescind(sponsor, invitation):
    invitation = _locked(invitation)
    require(sponsor, "invitation.rescind", invitation)
    _end(invitation, Status.RESCINDED, sponsor)
    log.record(sponsor, "invitation.rescind", invitation, {"invitee": invitation.invitee_id})
    return invitation


# --- invitee -------------------------------------------------------------------------------


def invitation_for_token(token):
    """The pending invitation a link refers to, or None if it is unknown, used, ended or expired."""
    invitation = Invitation.objects.filter(token_hash=hash_token(token), status=Status.PENDING).first()
    if invitation is None or timezone.now() >= expires_at(invitation):
        return None
    return invitation


def _unique_slug(display_name):
    base = slugify(display_name)[:70] or "member"
    slug, n = base, 1
    while User.objects.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


@transaction.atomic
def accept(token, display_name, password):
    """Follow the link and set a password: creates the account (status invited) with its email
    verified, since the link proves the address, and the IdentityRecord. The invitation becomes
    accepted if it holds a slot, otherwise waitlisted."""
    found = invitation_for_token(token)
    if found is None:
        raise PermissionDenied("This invitation link is not valid.")
    invitation = _locked(found)
    if invitation.status != Status.PENDING:
        raise PermissionDenied("This invitation link is not valid.")
    display_name = display_name.strip()
    if not display_name:
        raise ValidationError("Choose a display name.")
    user = User(
        email=invitation.invitee_email, display_name=display_name, slug=_unique_slug(display_name),
        status=User.Status.INVITED,
    )
    validate_password(password, user)
    user.set_password(password)
    user.save()
    now = timezone.now()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    IdentityRecord.objects.create(user=user, email_verified_at=now, vouching_notes=invitation.vouching_notes)
    invitation.invitee, invitation.accepted_at = user, now
    invitation.status = Status.ACCEPTED if capacity.has_slot_for(invitation) else Status.WAITLISTED
    invitation.save(update_fields=["invitee", "accepted_at", "status"])
    log.record(None, "invitation.accept", invitation, {"invitee": user.pk, "status": invitation.status})
    _notify_sponsor(invitation, "invitation.accepted")
    return invitation


@transaction.atomic
def submit_identity(invitee, real_name):
    """The invitee's last step: their real name, after enrolling TOTP. Ready for review."""
    invitation = _locked(invitee.invitation)
    require(invitee, "onboarding.submit_identity", invitation)
    real_name = real_name.strip()
    if not real_name:
        raise ValidationError("Enter your real name.")
    identity = IdentityRecord.objects.select_for_update().get(user=invitee)
    identity.real_name, identity.submitted_at = real_name, timezone.now()
    identity.save(update_fields=["real_name", "submitted_at"])
    log.record(None, "invitation.submit_identity", invitation, {"invitee": invitee.pk})
    return identity


@transaction.atomic
def invitee_decline(invitation, *, invitee=None, token=None):
    """The invitee declines: signed in after accepting, or on the link page before accepting."""
    if invitee is not None:
        invitation = _locked(invitation)
        require(invitee, "invitation.invitee_decline", invitation)
    else:
        found = invitation_for_token(token or "")
        if found is None or found.pk != invitation.pk:
            raise PermissionDenied("This invitation link is not valid.")
        invitation = _locked(found)
    _end(invitation, Status.INVITEE_DECLINED, invitee)
    log.record(None, "invitation.invitee_decline", invitation, {"invitee": invitation.invitee_id})
    _notify_sponsor(invitation, "invitation.invitee_declined")
    return invitation


# --- Admin review --------------------------------------------------------------------------


@transaction.atomic
def approve(actor, invitation):
    """The identity check passes: the account becomes a Guest with the guest role, and the
    invitation's slot becomes the first Sponsorship. Approving a waitlisted invitation is an
    over-cap approval and is audited as one."""
    invitation = _locked(invitation)
    require(actor, "invitation.approve", invitation)
    over_cap = invitation.status == Status.WAITLISTED
    now = timezone.now()
    member = invitation.invitee
    invitation.status, invitation.decided_by, invitation.decided_at = Status.APPROVED, actor, now
    invitation.save(update_fields=["status", "decided_by", "decided_at"])
    member.status, member.joined_at = User.Status.GUEST, now
    member.save(update_fields=["status", "joined_at"])
    RoleAssignment.objects.create(
        user=member, role=Role.objects.get(name=roles.GUEST), granted_by=actor, granted_at=now,
        reason=f"Invitation {invitation.pk} approved",
    )
    sponsor_role = roles.trust_role(invitation.sponsor)
    Sponsorship.objects.create(
        sponsor=invitation.sponsor, member=member, sponsor_role=sponsor_role, started_at=now, is_original=True
    )
    IdentityRecord.objects.filter(user=member).update(reviewed_by=actor, reviewed_at=now)
    log.record(
        actor, "invitation.approve_over_cap" if over_cap else "invitation.approve", invitation,
        {"invitee": member.pk, "sponsor": invitation.sponsor_id},
    )
    _notify_sponsor(invitation, "invitation.approved")
    return invitation


@transaction.atomic
def decline(actor, invitation, reason=""):
    """The identity check fails. The sponsor is told; the account can no longer sign in."""
    invitation = _locked(invitation)
    require(actor, "invitation.decline", invitation)
    IdentityRecord.objects.filter(user_id=invitation.invitee_id).update(reviewed_by=actor, reviewed_at=timezone.now())
    _end(invitation, Status.DECLINED, actor)
    log.record(actor, "invitation.decline", invitation, {"invitee": invitation.invitee_id, "reason": reason})
    _notify_sponsor(invitation, "invitation.declined")
    return invitation


def review_queue():
    """Invitations waiting on an Admin or Owner, longest-waiting first."""
    return (
        Invitation.objects.filter(
            status__in=[Status.ACCEPTED, Status.WAITLISTED], invitee__identity__submitted_at__isnull=False
        )
        .select_related("sponsor", "invitee", "invitee__identity")
        .order_by("invitee__identity__submitted_at")
    )


@transaction.atomic
def comp(actor, member):
    """Complimentary membership: Guest to Provisional without payment (design rule 20)."""
    require(actor, "subscription.comp", member)
    now = timezone.now()
    subscription, _ = Subscription.objects.select_for_update().get_or_create(user=member)
    subscription.status, subscription.comped_by, subscription.comped_at = Subscription.Status.COMPED, actor, now
    # Comps granted before billing launches become founding comps (billing.lapse.launch).
    subscription.comp_reason = Subscription.CompReason.OTHER
    subscription.read_only_at = None
    subscription.save()
    for assignment in RoleAssignment.objects.filter(
        user=member, role__name=roles.GUEST, revoked_at__isnull=True, scope_subforum__isnull=True
    ):
        assignment.revoked_at, assignment.revoked_by = now, actor
        assignment.save()
    RoleAssignment.objects.create(
        user=member, role=Role.objects.get(name=roles.PROVISIONAL), granted_by=actor, granted_at=now,
        reason="Complimentary membership",
    )
    member.status = User.Status.ACTIVE
    member.save(update_fields=["status"])
    log.record(actor, "subscription.comp", subscription, {"member": member.pk})
    return subscription


# --- jobs ----------------------------------------------------------------------------------


def expire_pending(now=None):
    """Expire pending invitations older than invitation.expiry_days. Accepted and waitlisted ones
    never expire (design rule 18). Returns how many expired."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=registry.site_value("invitation.expiry_days"))
    count = 0
    for pk in Invitation.objects.filter(status=Status.PENDING, created_at__lte=cutoff).values_list("pk", flat=True):
        with transaction.atomic():
            invitation = Invitation.objects.select_for_update().select_related("sponsor").get(pk=pk)
            if invitation.status != Status.PENDING:
                continue
            _end(invitation, Status.EXPIRED, None)
            log.record(None, "invitation.expire", invitation)
            count += 1
    return count


def delete_ended_accounts(now=None):
    """Delete invited accounts whose invitation ended without approval more than
    invitation.ended_account_deletion_days ago, with their identity details. The Invitation row and
    audit entries stay (design rule 19). Returns how many were deleted."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=registry.site_value("invitation.ended_account_deletion_days"))
    ended = Invitation.objects.filter(
        status__in=Invitation.ENDED, decided_at__lte=cutoff, invitee__isnull=False,
        invitee__status=User.Status.REMOVED,
    ).values_list("pk", flat=True)
    count = 0
    for pk in ended:
        with transaction.atomic():
            invitation = Invitation.objects.select_for_update().get(pk=pk)
            user = invitation.invitee
            if user is None or user.status != User.Status.REMOVED:
                continue
            user_pk = user.pk
            IdentityRecord.objects.filter(user=user).delete()
            Notification.objects.filter(recipient=user).delete()
            user.delete()
            log.record(None, "invitation.account_deleted", invitation, {"invitee": user_pk})
            count += 1
    return count
