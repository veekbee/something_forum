from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from billing import services, stripe_api, webhooks
from billing.models import Subscription
from core.permissions import can


def billing_page(request):
    """Visible only to the member (rule 50): status, period end, any read-only date, and the way to
    pay or manage the card."""
    decision = can(request.user, "billing.view", request.user)
    if not decision:
        raise PermissionDenied(decision.reason)
    from billing.models import Gift

    return render(request, "billing/billing.html", {
        "sub": Subscription.objects.filter(user=request.user).first(),
        "gifts": [g for g in Gift.objects.filter(invitee=request.user, status=Gift.Status.PAID).select_related("sponsor")
                  if can(request.user, "billing.accept_gift", g)],
        "may_pay": can(request.user, "billing.pay_membership"),
        "may_manage": can(request.user, "billing.portal"),
        "done": request.GET.get("done"),
    })


def ban_payment(request):
    """The one extra page a banned member reaches (rule 45): the fee, and the way to pay it."""
    from billing import bans

    ban = bans.active_ban(request.user)
    decision = can(request.user, "billing.pay_ban", ban) if ban else None
    if not decision:
        raise PermissionDenied(decision.reason if decision is not None else "You are not banned.")
    if request.method == "POST":
        back = request.build_absolute_uri(reverse("ban_payment"))
        return redirect(bans.ban_checkout(request.user, ban, f"{back}?done=1", back))
    return render(request, "billing/ban_payment.html", {
        "ban": ban, "fee_dollars": bans.fee_cents(request.user) / 100, "done": request.GET.get("done"),
    })


@require_POST
def pay(request):
    back = request.build_absolute_uri(reverse("billing"))
    return redirect(services.membership_checkout(request.user, f"{back}?done=1", back))


@require_POST
def manage(request):
    return redirect(services.portal_url(request.user, request.build_absolute_uri(reverse("billing"))))


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Public by design (rule 9); trusted only once Stripe's signature checks out."""
    import json

    import stripe

    payload = request.body
    try:
        stripe_api.verify_webhook(payload, request.headers.get("Stripe-Signature"))
    except (stripe.SignatureVerificationError, ValueError):
        return HttpResponseBadRequest("bad signature")
    webhooks.process(json.loads(payload))
    return HttpResponse("ok")


@require_POST
def gift(request, user_pk):
    from accounts.models import User
    from billing import extras

    invitee = get_object_or_404(User, pk=user_pk)
    back = request.build_absolute_uri(reverse("invitations"))
    return redirect(extras.gift_checkout(request.user, invitee, back, back))


@require_POST
def accept_gift(request, pk):
    from billing import extras
    from billing.models import Gift

    extras.accept_gift(request.user, get_object_or_404(Gift, pk=pk))
    return redirect("billing")


def extras_page(request):
    from billing import extras
    from billing.models import Extra

    from boards.emoji import credit

    rows = [{"extra": e, "owned": extras.has_extra(request.user, e.key), "may_buy": can(request.user, "extras.buy", e),
             "credit": credit(request.user) if e.key == "custom_emoji" else None}
            for e in Extra.objects.filter(is_active=True).order_by("name")]
    return render(request, "billing/extras.html", {"rows": rows})


@require_POST
def buy_extra(request, key):
    from billing import extras
    from billing.models import Extra

    extra = get_object_or_404(Extra, key=key)
    back = request.build_absolute_uri(reverse("extras"))
    return redirect(extras.extra_checkout(request.user, extra, f"{back}?done=1", back))


def customise(request):
    """The member's own avatar and caption, for those who bought the extra."""
    from django.core.exceptions import ValidationError

    from billing import extras

    decision = can(request.user, "profile.customise")
    if not decision:
        raise PermissionDenied(decision.reason)
    errors = []
    if request.method == "POST":
        try:
            extras.set_avatar_and_caption(request.user, request.FILES.get("avatar"), request.POST.get("caption", ""))
        except ValidationError as exc:
            errors = exc.messages
        else:
            return redirect("member_profile", slug=request.user.slug)
    return render(request, "billing/customise.html", {"errors": errors})
