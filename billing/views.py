from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
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
    return render(request, "billing/billing.html", {
        "sub": Subscription.objects.filter(user=request.user).first(),
        "may_pay": can(request.user, "billing.pay_membership"),
        "may_manage": can(request.user, "billing.portal"),
        "done": request.GET.get("done"),
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
