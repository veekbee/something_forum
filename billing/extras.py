"""Gifts and paid extras (docs/DESIGN.md, Gifts and Paid extras; rules 41 and 49)."""

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit import log
from billing import membership
from billing.models import Charge, Entitlement, Extra, Gift
from billing.services import checkout
from billing.webhooks import register_purpose
from core import registry
from core.dates import add_months
from core.models import Notification
from core.services import require

# --- gifts ---------------------------------------------------------------------------------


def gift_checkout(sponsor, invitee, success_url, cancel_url):
    """A sponsor pays their own approved Guest invitee's first year, once. The sponsor's card is
    never attached to another member's subscription: this is a one-off payment."""
    require(sponsor, "billing.gift", invitee)
    return checkout(
        sponsor, purpose="gift", mode="payment", success_url=success_url, cancel_url=cancel_url,
        line_items=[{"price": settings.STRIPE_PRICE_GIFT, "quantity": 1}], metadata={"invitee": str(invitee.pk)},
    )


@register_purpose("gift")
def gift_paid(sponsor, session):
    from accounts.models import User

    invitee = User.objects.filter(pk=int((session.get("metadata") or {}).get("invitee", 0))).first()
    if invitee is None:
        return
    charge, created = Charge.objects.get_or_create(
        stripe_payment_intent_id=session.get("payment_intent") or session["id"],
        defaults={"user": sponsor, "kind": Charge.Kind.GIFT, "amount_cents": session.get("amount_total", 0),
                  "currency": session.get("currency", "usd"), "status": "succeeded"},
    )
    if not created:
        return
    gift = Gift.objects.create(sponsor=sponsor, invitee=invitee, charge=charge)
    log.record(None, "billing.gift_paid", gift, {"sponsor": sponsor.pk, "invitee": invitee.pk})
    Notification.objects.create(recipient=invitee, kind="billing.gift", payload={"gift": gift.pk})


@transaction.atomic
def accept_gift(invitee, gift):
    """The gift counts as the Guest's payment: a first year with no Stripe subscription, moving them
    to Provisional now. The renewal is their own, set up when they first pay for themselves."""
    gift = Gift.objects.select_for_update().get(pk=gift.pk)
    require(invitee, "billing.accept_gift", gift)
    now = timezone.now()
    gift.status, gift.accepted_at = Gift.Status.ACCEPTED, now
    gift.save(update_fields=["status", "accepted_at"])
    membership.restore(invitee, add_months(now, 12), reason="gift")
    log.record(invitee, "billing.gift_accepted", gift)
    return gift


# --- extras --------------------------------------------------------------------------------


def has_extra(user, key):
    return Entitlement.objects.filter(user=user, extra__key=key, revoked_at__isnull=True).exists()


def extra_checkout(user, extra, success_url, cancel_url):
    require(user, "extras.buy", extra)
    return checkout(
        user, purpose="extra", mode="payment", success_url=success_url, cancel_url=cancel_url,
        line_items=[{"price": getattr(settings, extra.stripe_price_setting), "quantity": 1}],
        metadata={"extra": extra.key},
    )


@register_purpose("extra")
def extra_paid(user, session):
    extra = Extra.objects.filter(key=(session.get("metadata") or {}).get("extra", "")).first()
    if extra is None:
        return
    charge, created = Charge.objects.get_or_create(
        stripe_payment_intent_id=session.get("payment_intent") or session["id"],
        defaults={"user": user, "kind": Charge.Kind.EXTRA, "amount_cents": session.get("amount_total", 0),
                  "currency": session.get("currency", "usd"), "status": "succeeded"},
    )
    if created:
        entitlement = Entitlement.objects.create(user=user, extra=extra, charge=charge)
        log.record(None, "billing.extra_bought", entitlement, {"extra": extra.key})


@transaction.atomic
def set_avatar_and_caption(user, upload=None, caption=None):
    """The avatar is handled like post images: re-encoded, metadata stripped, no SVG."""
    from boards import images
    from boards.models import Attachment

    require(user, "profile.customise")
    if caption is not None:
        caption = " ".join(caption.split())
        limit = registry.site_value("extras.caption_max_chars")
        if len(caption) > limit:
            raise ValidationError(f"A caption can be at most {limit} characters.")
        user.caption = caption
    if upload is not None:
        max_mb = registry.get("subforum.max_image_mb").default
        if upload.size > max_mb * 1024 * 1024:
            raise ValidationError(f"An avatar can be at most {max_mb} MB.")
        data, ext, mime, width, height = images.reencode(upload)
        key = default_storage.save(f"avatars/{user.pk}-{timezone.now():%Y%m%d%H%M%S}.{ext}", ContentFile(data))
        user.avatar = Attachment.objects.create(
            post=None, uploader=user, storage_key=key, filename=upload.name[:255], mime=mime,
            size_bytes=len(data), width=width, height=height,
        )
    user.save(update_fields=["caption", "avatar"])
    return user


def _record(actor, member, kind, internal_reason, *, public_summary="", is_public, related_action=None):
    """These actions are single-actor: the person who may take them takes them alone (rule 49)."""
    from moderation.models import ModerationAction

    now = timezone.now()
    action = ModerationAction.objects.create(
        target_user=member, kind=kind, initiated_by=actor, status=ModerationAction.Status.ACTIVE, starts_at=now,
        internal_reason=internal_reason, public_summary_draft=public_summary, public_summary=public_summary,
        is_public=is_public, related_action=related_action,
    )
    if is_public:
        Notification.objects.create(recipient=member, kind="moderation.action", payload={"action": action.pk, "kind": kind})
    return action


@transaction.atomic
def reset_to_default(actor, member, reason_key, note=""):
    """Any staff member resets an offending avatar or caption, with a preset reason. The extra itself
    is not refunded or removed, and the reset is not on the Rap Sheet."""
    from moderation.models import ModerationAction, reason_text

    require(actor, "profile.reset_extra", member)
    reason = reason_text(reason_key, note)
    member.avatar, member.caption = None, ""
    member.save(update_fields=["avatar", "caption"])
    action = _record(actor, member, ModerationAction.Kind.AVATAR_RESET, reason, is_public=False)
    log.record(actor, "profile.reset_extra", member, {"action": action.pk, "reason": reason})
    return action


@transaction.atomic
def revoke(actor, entitlement, internal_reason, public_summary):
    """An Admin or Owner revokes an extra for repeated misuse: a public action, not refunded."""
    from moderation.models import ModerationAction

    entitlement = Entitlement.objects.select_for_update().select_related("user").get(pk=entitlement.pk)
    require(actor, "extras.revoke", entitlement)
    if not internal_reason.strip() or not public_summary.strip():
        raise ValidationError("Give the reason, for staff, and the public summary.")
    member = entitlement.user
    action = _record(actor, member, ModerationAction.Kind.EXTRA_REVOCATION, internal_reason.strip(),
                     public_summary=public_summary.strip(), is_public=True)
    entitlement.revoked_at, entitlement.revoked_by_action = timezone.now(), action
    entitlement.save(update_fields=["revoked_at", "revoked_by_action"])
    member.avatar, member.caption = None, ""
    member.save(update_fields=["avatar", "caption"])
    log.record(actor, "billing.extra_revoked", entitlement, {"action": action.pk})
    return action


@transaction.atomic
def restore(actor, entitlement, internal_reason, public_summary):
    """An Admin or Owner reverses a revocation. Both stay on the record: the revocation marked
    reversed, and the restoration as its own entry."""
    from moderation.models import ModerationAction

    entitlement = Entitlement.objects.select_for_update(of=("self",)).select_related("user", "revoked_by_action").get(
        pk=entitlement.pk)
    require(actor, "extras.restore", entitlement)
    if not internal_reason.strip() or not public_summary.strip():
        raise ValidationError("Give the reason, for staff, and the public summary.")
    revocation = entitlement.revoked_by_action
    if revocation is not None and revocation.kind == ModerationAction.Kind.EXTRA_REVOCATION:
        ModerationAction.objects.filter(pk=revocation.pk).update(
            status=ModerationAction.Status.REVERSED, ends_at=timezone.now())
    action = _record(actor, entitlement.user, ModerationAction.Kind.EXTRA_RESTORATION, internal_reason.strip(),
                     public_summary=public_summary.strip(), is_public=True, related_action=revocation)
    entitlement.revoked_at = None
    entitlement.save(update_fields=["revoked_at"])
    log.record(actor, "billing.extra_restored", entitlement, {"action": action.pk})
    return action
