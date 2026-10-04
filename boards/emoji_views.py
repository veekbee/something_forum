"""Custom emoji pages (rule 62). Decisions are made by core.permissions and boards.emoji."""

import io

from django import forms
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from boards import emoji as service
from boards.models import CustomEmoji
from core.permissions import can


def _errors(exc):
    return exc.messages if isinstance(exc, ValidationError) else [str(exc)]


class EmojiForm(forms.Form):
    name = forms.CharField(max_length=32, help_text="2 to 32 lowercase letters, digits or underscores; typed as :name:")
    image = forms.ImageField(help_text="PNG, GIF, WebP or JPEG; at most 128 pixels high and three times as wide as tall")


def emoji_list(request):
    """The forum's emoji, who added each, and for staff the ones waiting or retired."""
    staff = bool(can(request.user, "emoji.retire", CustomEmoji(status=CustomEmoji.Status.APPROVED)))
    approved = CustomEmoji.objects.filter(status=CustomEmoji.Status.APPROVED).select_related("purchaser").order_by("name")
    mine = CustomEmoji.objects.filter(purchaser=request.user).exclude(status=CustomEmoji.Status.APPROVED).order_by("-created_at")
    return render(request, "boards/emoji/list.html", {
        "approved": approved, "mine": mine, "staff": staff, "may_submit": bool(can(request.user, "emoji.submit")),
        "retired": CustomEmoji.objects.filter(status=CustomEmoji.Status.RETIRED).order_by("name") if staff else [],
        "error": request.GET.get("error", ""),
    })


def emoji_new(request):
    decision = can(request.user, "emoji.submit")
    if not decision:
        raise PermissionDenied(decision.reason)
    form = EmojiForm(request.POST or None, request.FILES or None)
    errors = []
    found = service.credit(request.user)
    if request.method == "POST" and form.is_valid():
        try:
            service.submit(request.user, form.cleaned_data["name"], request.FILES["image"])
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            return redirect("emoji_list")
    return render(request, "boards/emoji/new.html", {
        "form": form, "errors": errors, "replaces": found[1] if found else None,
    })


def _viewable(request, pk):
    emoji = get_object_or_404(CustomEmoji.objects.select_related("image"), pk=pk)
    if not can(request.user, "emoji.view_image", emoji):
        raise Http404
    return emoji


@require_GET
def emoji_image(request, pk):
    emoji = _viewable(request, pk)
    from django.conf import settings

    if settings.AWS_STORAGE_BUCKET_NAME:
        return HttpResponseRedirect(default_storage.url(emoji.image.storage_key))
    response = FileResponse(default_storage.open(emoji.image.storage_key, "rb"), content_type=emoji.image.mime)
    response["Cache-Control"] = "private, max-age=3600"
    return response


@require_GET
def emoji_still(request, pk):
    """The first frame as a PNG, for the still setting and for reduced motion."""
    from PIL import Image

    emoji = _viewable(request, pk)
    key = f"emoji:still:{emoji.pk}:{emoji.image.storage_key}"
    data = cache.get(key)
    if data is None:
        with default_storage.open(emoji.image.storage_key, "rb") as handle:
            image = Image.open(io.BytesIO(handle.read()))
            image.seek(0)
            out = io.BytesIO()
            image.convert("RGBA").save(out, "PNG")
        data = out.getvalue()
        cache.set(key, data, timeout=None)
    response = HttpResponse(data, content_type="image/png")
    response["Cache-Control"] = "private, max-age=3600"
    return response


def _back(request, fn):
    try:
        fn()
    except (ValidationError, PermissionDenied) as exc:
        target = request.POST.get("next") or reverse("emoji_list")
        if not target.startswith("/"):
            target = reverse("emoji_list")
        return redirect(f"{target}?{urlencode({'error': ' '.join(_errors(exc))})}")
    target = request.POST.get("next", "")
    return redirect(target if target.startswith("/") else reverse("emoji_list"))


@require_POST
def emoji_review(request, pk, step):
    emoji = get_object_or_404(CustomEmoji, pk=pk)
    if step == "approve":
        return _back(request, lambda: service.approve(request.user, emoji))
    if step == "reject":
        return _back(request, lambda: service.reject(request.user, emoji, request.POST.get("reason", "")))
    raise PermissionDenied("unknown step")


@require_POST
def emoji_retire(request, pk):
    emoji = get_object_or_404(CustomEmoji, pk=pk)
    return _back(request, lambda: service.retire(request.user, emoji))


@require_POST
def emoji_refund(request, pk):
    from billing.models import Entitlement

    entitlement = get_object_or_404(Entitlement, pk=pk)
    return _back(request, lambda: service.refund(request.user, entitlement))


def display_settings(request):
    """The member's own choice of how custom emoji display (rule 62)."""
    choices = User.EmojiDisplay
    if request.method == "POST" and request.POST.get("emoji_display") in choices.values:
        User.objects.filter(pk=request.user.pk).update(emoji_display=request.POST["emoji_display"])
        return redirect("display_settings")
    return render(request, "boards/emoji/display.html", {"choices": choices.choices,
                                                          "current": request.user.emoji_display})
