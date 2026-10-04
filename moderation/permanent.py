"""The Permanent Ban (docs/DESIGN.md, Permanent Ban; rule 46). It applies to the person, not the
account, and cannot be bought back. Only an Owner imposes or annuls one. The account can no longer
sign in; its posts stay. The person's verified email addresses and real name go on the
permanent-ban list, which only Admins and Owners see and every invitation is checked against."""

import json

from allauth.account.models import EmailAddress
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import IdentityRecord
from audit import log
from core.services import require
from moderation.models import ModerationAction, PermanentBanRecord


def is_permanently_banned(user):
    return ModerationAction.objects.in_force().filter(
        target_user=user, kind=ModerationAction.Kind.PERMANENT_BAN
    ).exists()


def _normalise(text):
    return " ".join((text or "").split()).casefold()


def write_list_entry(action):
    """Called when a Permanent Ban takes effect, in the same transaction."""
    user = action.target_user
    emails = sorted({e.email.casefold() for e in EmailAddress.objects.filter(user=user, verified=True)}
                    | {user.email.casefold()})
    identity = IdentityRecord.objects.filter(user=user).first()
    return PermanentBanRecord.objects.create(
        action=action, emails=json.dumps(emails), real_name=identity.real_name if identity else ""
    )


def matches(email, real_name=""):
    """List entries an invitation matches, by email or real name. Shown to the reviewing Admin, never
    acted on automatically."""
    email, name = (email or "").casefold(), _normalise(real_name)
    found = []
    for record in PermanentBanRecord.objects.filter(annulled_at__isnull=True).select_related("action__target_user"):
        if email and email in json.loads(record.emails or "[]"):
            found.append(record)
        elif name and name == _normalise(record.real_name):
            found.append(record)
    return found


@transaction.atomic
def impose(owner, member, internal_reason, public_summary=""):
    from moderation import services

    return services.initiate_action(owner, member, ModerationAction.Kind.PERMANENT_BAN,
                                    internal_reason=internal_reason, public_summary=public_summary)


@transaction.atomic
def annul(owner, action, reason):
    """Only to correct an error, never as forgiveness; the written reason is required and audited."""
    action = ModerationAction.objects.select_for_update(of=("self",)).get(pk=action.pk)
    require(owner, "moderation.annul", action)
    if not reason.strip():
        raise ValidationError("Write why this Permanent Ban was an error.")
    action.status, action.ends_at = ModerationAction.Status.ANNULLED, timezone.now()
    action.save(update_fields=["status", "ends_at"])
    PermanentBanRecord.objects.filter(action=action).update(annulled_at=timezone.now())
    log.record(owner, "moderation.annul", action, {"reason": reason.strip()})
    return action
