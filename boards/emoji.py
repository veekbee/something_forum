"""Custom emoji (docs/DESIGN.md, Custom emoji; rule 62).

A member buys the custom_emoji extra, which gives them a credit, then submits a name and an image
against it. Any staff member approves or rejects. A rejected emoji's charge pays for one
resubmission; an Admin or Owner can refund instead in a particular case. Staff can retire an
approved emoji that turns out to be a problem, with no refund.

Posts store `:name:` wrapped in a span when they are rendered (boards.rendering). Each response then
shows approved emoji as images, still images or names, by the reader's emoji_display setting, on top
of the stored HTML (rule 21); pending, rejected and retired emoji stay as text."""

import io
import re

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from audit import log
from boards.models import Attachment, CustomEmoji
from core import registry
from core.models import Notification
from core.services import require

NAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")
# `:name:` in post text, not part of a longer run such as a time (10:30:45).
TEXT_RE = re.compile(r"(?<![\w:]):([a-z0-9_]{2,32}):(?![\w:])")
SPAN_RE = re.compile(r'<span class="emoji-code" data-emoji="([a-z0-9_]{2,32})">:\1:</span>')
Status = CustomEmoji.Status


# --- credits -------------------------------------------------------------------------------


def credit(user):
    """(entitlement, rejected emoji it may replace or None) for an unused purchase, or None."""
    from billing.models import Entitlement

    for entitlement in Entitlement.objects.filter(
        user=user, extra__key="custom_emoji", revoked_at__isnull=True
    ).select_related("charge").order_by("granted_at", "pk"):
        if entitlement.charge is None or entitlement.charge.status == "refunded":
            continue
        used = list(CustomEmoji.objects.filter(charge=entitlement.charge).order_by("created_at", "pk"))
        if not used:
            return entitlement, None
        if len(used) == 1 and used[0].status == Status.REJECTED:
            return entitlement, used[0]
    return None


# --- submitting ----------------------------------------------------------------------------


def _image(upload):
    from PIL import Image

    from boards import images

    if upload.size > registry.site_value("emoji.max_kb") * 1024:
        raise ValidationError(f"An emoji image can be at most {registry.site_value('emoji.max_kb')} KB.")
    data, ext, mime, width, height = images.reencode(upload)
    frames = getattr(Image.open(io.BytesIO(data)), "n_frames", 1)
    if frames > 1 and not registry.site_value("emoji.allow_animated"):
        raise ValidationError("Animated emoji are not accepted.")
    if height > registry.site_value("emoji.max_height_px"):
        raise ValidationError(f"An emoji image can be at most {registry.site_value('emoji.max_height_px')} pixels high.")
    if width > height * registry.site_value("emoji.max_aspect_ratio"):
        raise ValidationError(
            f"An emoji can be at most {registry.site_value('emoji.max_aspect_ratio')} times as wide as it is tall.")
    return data, ext, mime, width, height


@transaction.atomic
def submit(user, name, upload):
    require(user, "emoji.submit")
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ValidationError("A name is 2 to 32 lowercase letters, digits or underscores.")
    if CustomEmoji.objects.filter(name=name).exists():
        raise ValidationError(f":{name}: is taken.")
    found = credit(user)
    if found is None:
        raise ValidationError("Buy the custom emoji extra first.")
    entitlement, replaces = found
    data, ext, mime, width, height = _image(upload)
    key = default_storage.save(f"emoji/{name}-{timezone.now():%Y%m%d%H%M%S}.{ext}", ContentFile(data))
    image = Attachment.objects.create(post=None, uploader=user, storage_key=key, filename=upload.name[:255],
                                      mime=mime, size_bytes=len(data), width=width, height=height)
    emoji = CustomEmoji.objects.create(name=name, image=image, purchaser=user, charge=entitlement.charge,
                                       resubmission_of=replaces)
    log.record(user, "emoji.submit", emoji, {"name": name, "resubmission_of": replaces.pk if replaces else None})
    return emoji


# --- staff decisions -----------------------------------------------------------------------


def _locked(emoji):
    return CustomEmoji.objects.select_for_update(of=("self",)).select_related("purchaser").get(pk=emoji.pk)


def _tell(emoji, kind, **extra):
    Notification.objects.create(recipient=emoji.purchaser, kind=kind, payload={"emoji": emoji.pk, "name": emoji.name, **extra})


@transaction.atomic
def approve(actor, emoji):
    emoji = _locked(emoji)
    require(actor, "emoji.review", emoji)
    emoji.status, emoji.reviewed_by, emoji.reviewed_at = Status.APPROVED, actor, timezone.now()
    emoji.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    log.record(actor, "emoji.approve", emoji, {"name": emoji.name})
    _tell(emoji, "emoji.approved")
    return emoji


@transaction.atomic
def reject(actor, emoji, reason):
    emoji = _locked(emoji)
    require(actor, "emoji.review", emoji)
    if not (reason or "").strip():
        raise ValidationError("Say why, for the member.")
    emoji.status, emoji.reviewed_by, emoji.reviewed_at = Status.REJECTED, actor, timezone.now()
    emoji.reject_reason = reason.strip()
    emoji.save(update_fields=["status", "reviewed_by", "reviewed_at", "reject_reason"])
    log.record(actor, "emoji.reject", emoji, {"name": emoji.name})
    _tell(emoji, "emoji.rejected", reason=emoji.reject_reason)
    return emoji


@transaction.atomic
def retire(actor, emoji):
    """Posts that used it show the plain :name: text again. No refund."""
    emoji = _locked(emoji)
    require(actor, "emoji.retire", emoji)
    emoji.status, emoji.retired_by, emoji.retired_at = Status.RETIRED, actor, timezone.now()
    emoji.save(update_fields=["status", "retired_by", "retired_at"])
    log.record(actor, "emoji.retire", emoji, {"name": emoji.name})
    _tell(emoji, "emoji.retired")
    return emoji


@transaction.atomic
def refund(actor, entitlement):
    """An Admin or Owner refunds an emoji purchase in a particular case, while no emoji bought with it
    is pending or live. The credit is used up."""
    from billing import stripe_api
    from billing.models import Charge, Entitlement

    entitlement = Entitlement.objects.select_for_update(of=("self",)).select_related("charge", "extra").get(
        pk=entitlement.pk)
    require(actor, "emoji.refund", entitlement)
    charge = entitlement.charge
    stripe_api.refund(charge.stripe_payment_intent_id)
    Charge.objects.filter(pk=charge.pk).update(status="refunded")
    entitlement.revoked_at = timezone.now()
    entitlement.save(update_fields=["revoked_at"])
    log.record(actor, "emoji.refund", entitlement, {"charge": charge.pk, "amount_cents": charge.amount_cents})
    Notification.objects.create(recipient_id=entitlement.user_id, kind="emoji.refunded", payload={})
    return entitlement


# --- showing emoji -------------------------------------------------------------------------


def _img(emoji, mode):
    src = reverse("emoji_image", args=[emoji.pk])
    still = reverse("emoji_still", args=[emoji.pk])
    if mode == "still":
        return format_html('<img class="emoji" src="{}" alt="{}" title=":{}:">', still, emoji.name, emoji.name)
    if emoji.image.mime == "image/gif":
        # Members whose device asks for reduced motion see the first frame.
        return format_html('<picture><source srcset="{}" media="(prefers-reduced-motion: reduce)">'
                           '<img class="emoji" src="{}" alt="{}" title=":{}:"></picture>',
                           still, src, emoji.name, emoji.name)
    return format_html('<img class="emoji" src="{}" alt="{}" title=":{}:">', src, emoji.name, emoji.name)


def for_request(request, html):
    """Approved emoji as images, by the reader's setting; everything else stays as text."""
    user = getattr(request, "user", None)
    mode = getattr(user, "emoji_display", "images")
    if mode == "names" or 'class="emoji-code"' not in html:
        return html
    names = set(SPAN_RE.findall(html))
    cache = getattr(request, "_emoji", None)
    if cache is None:
        cache = request._emoji = {}
    missing = names - set(cache)
    if missing:
        found = {e.name: e for e in CustomEmoji.objects.filter(name__in=missing, status=Status.APPROVED)
                 .select_related("image")}
        cache.update({name: found.get(name) for name in missing})

    def swap(match):
        emoji = cache.get(match.group(1))
        return _img(emoji, mode) if emoji is not None else match.group(0)

    return SPAN_RE.sub(swap, html)
