"""Build step 5 part 1: Checkout, the billing page and webhooks (rules 40, 41, 43 and 50). Stripe is
never called: payloads are shaped like Stripe's, and the Stripe client is replaced."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

from accounts import roles
from accounts.models import User
from billing import stripe_api, webhooks
from billing.models import Charge, StripeEvent, Subscription
from core.models import Notification
from core.permissions import can
from tests.factories import enrol_totp

YEAR_END = int(datetime(2027, 10, 4, tzinfo=dt_timezone.utc).timestamp())
_ids = iter(range(1, 10**6))


def event(type_, obj):
    return {"id": f"evt_{next(_ids)}", "type": type_, "data": {"object": obj}}


def checkout_membership(user, sub_id="sub_1", customer="cus_1"):
    return event("checkout.session.completed", {
        "id": f"cs_{next(_ids)}", "mode": "subscription", "payment_status": "paid", "customer": customer,
        "subscription": sub_id, "metadata": {"purpose": "membership", "user": str(user.pk)},
    })


def invoice_paid(user, sub_id="sub_1", customer="cus_1", period_end=YEAR_END, amount=2500):
    return event("invoice.paid", {
        "id": f"in_{next(_ids)}", "customer": customer, "amount_paid": amount, "currency": "usd",
        "payment_intent": f"pi_{next(_ids)}",
        "parent": {"subscription_details": {"subscription": sub_id, "metadata": {"user": str(user.pk)}}},
        "lines": {"data": [{"period": {"start": period_end - 31536000, "end": period_end}}]},
    })


@pytest.fixture
def guest(make_user):
    return make_user("guest")


# --- webhooks ------------------------------------------------------------------------------


def test_checkout_moves_a_guest_to_provisional(guest):
    assert webhooks.process(checkout_membership(guest))
    guest.refresh_from_db()
    assert guest.status == User.Status.ACTIVE and roles.trust_role(guest).name == "provisional"
    sub = Subscription.objects.get(user=guest)
    assert (sub.status, sub.stripe_customer_id, sub.stripe_subscription_id) == ("active", "cus_1", "sub_1")


def test_invoice_paid_sets_the_year_and_the_lapse_clock(guest):
    webhooks.process(checkout_membership(guest))
    webhooks.process(invoice_paid(guest))
    sub = Subscription.objects.get(user=guest)
    end = datetime.fromtimestamp(YEAR_END, tz=dt_timezone.utc)
    assert sub.current_period_end == end
    assert sub.read_only_at == end + timedelta(days=14)
    assert sub.restricted_at == sub.read_only_at + timedelta(days=90)
    charge = Charge.objects.get(user=guest)
    assert (charge.kind, charge.amount_cents) == ("subscription", 2500)


def test_the_same_event_twice_changes_nothing(guest):
    paid = invoice_paid(guest)
    webhooks.process(checkout_membership(guest))
    assert webhooks.process(paid) is True
    assert webhooks.process(paid) is False
    assert Charge.objects.count() == 1 and StripeEvent.objects.filter(event_id=paid["id"]).count() == 1


def test_events_out_of_order(guest):
    webhooks.process(invoice_paid(guest))  # the invoice arrives before the Checkout session
    guest.refresh_from_db()
    assert roles.trust_role(guest).name == "provisional"
    webhooks.process(checkout_membership(guest))
    sub = Subscription.objects.get(user=guest)
    assert sub.current_period_end == datetime.fromtimestamp(YEAR_END, tz=dt_timezone.utc)
    assert sub.stripe_subscription_id == "sub_1"


def test_an_older_period_never_shortens_the_year(guest):
    webhooks.process(invoice_paid(guest))
    webhooks.process(invoice_paid(guest, period_end=YEAR_END - 31536000))
    assert Subscription.objects.get(user=guest).current_period_end == datetime.fromtimestamp(YEAR_END, tz=dt_timezone.utc)


def test_payment_failed_marks_past_due_and_emails(guest, mailoutbox, django_capture_on_commit_callbacks):
    webhooks.process(checkout_membership(guest))
    with django_capture_on_commit_callbacks(execute=True):
        webhooks.process(event("invoice.payment_failed", {"id": "in_x", "customer": "cus_1",
                                                          "parent": {"subscription_details": {"subscription": "sub_1"}}}))
    assert Subscription.objects.get(user=guest).status == "past_due"
    assert Notification.objects.filter(recipient=guest, kind="billing.payment_failed").exists()
    assert mailoutbox and "card" not in mailoutbox[0].body.lower()


def test_subscription_updated_and_deleted(guest):
    webhooks.process(checkout_membership(guest))
    webhooks.process(event("customer.subscription.updated", {
        "id": "sub_1", "customer": "cus_1", "status": "active", "cancel_at_period_end": True,
        "items": {"data": [{"current_period_end": YEAR_END}]},
    }))
    sub = Subscription.objects.get(user=guest)
    assert sub.cancel_at_period_end and sub.current_period_end == datetime.fromtimestamp(YEAR_END, tz=dt_timezone.utc)
    webhooks.process(event("customer.subscription.deleted", {"id": "sub_1", "customer": "cus_1"}))
    sub.refresh_from_db()
    assert sub.stripe_subscription_id == "" and sub.read_only_at is not None  # the forum's clock still decides


def test_unhandled_events_are_recorded_and_ignored(guest):
    assert webhooks.process(event("customer.created", {"id": "cus_9"}))
    assert StripeEvent.objects.get(type="customer.created").processed_at is not None


def test_unpaid_checkout_does_nothing(guest):
    unpaid = checkout_membership(guest)
    unpaid["data"]["object"]["payment_status"] = "unpaid"
    webhooks.process(unpaid)
    guest.refresh_from_db()
    assert guest.status == User.Status.GUEST


# --- the endpoint --------------------------------------------------------------------------


def _signed(payload, secret):
    stamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{stamp}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={stamp},v1={signature}"


def test_endpoint_is_public_and_checks_the_signature(client, guest, settings):
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
    payload = json.dumps(checkout_membership(guest))
    assert client.post("/billing/stripe/webhook/", payload, content_type="application/json",
                       HTTP_STRIPE_SIGNATURE="t=1,v1=forged").status_code == 400
    response = client.post("/billing/stripe/webhook/", payload, content_type="application/json",
                           HTTP_STRIPE_SIGNATURE=_signed(payload, "whsec_test"))
    assert response.status_code == 200
    guest.refresh_from_db()
    assert roles.trust_role(guest).name == "provisional"


# --- Checkout and the billing page ---------------------------------------------------------


@pytest.fixture
def fake_stripe(monkeypatch):
    calls = []

    def create(**params):
        calls.append(params)
        return {"url": "https://checkout.stripe.test/session"}

    monkeypatch.setattr(stripe_api, "create_checkout_session", create)
    monkeypatch.setattr(stripe_api, "create_portal_session",
                        lambda customer, return_url: {"url": f"https://billing.stripe.test/{customer}"})
    return calls


def test_paying_starts_an_annual_checkout_with_the_configured_price(client, guest, fake_stripe, settings):
    settings.STRIPE_PRICE_MEMBERSHIP = "price_year"
    enrol_totp(guest)
    client.force_login(guest)
    response = client.post("/billing/pay/")
    assert response["Location"] == "https://checkout.stripe.test/session"
    [params] = fake_stripe
    assert params["mode"] == "subscription" and params["line_items"] == [{"price": "price_year", "quantity": 1}]
    assert params["metadata"] == {"purpose": "membership", "user": str(guest.pk)}
    assert params["automatic_tax"] == {"enabled": True} and params["billing_address_collection"] == "required"
    assert "trial_period_days" not in params.get("subscription_data", {})


def test_who_may_pay(make_user, guest):
    assert can(guest, "billing.pay_membership")
    comped = make_user("provisional")
    Subscription.objects.create(user=comped, status="comped")
    assert not can(comped, "billing.pay_membership")
    renewing = make_user("full")
    Subscription.objects.create(user=renewing, status="active", stripe_subscription_id="sub_9")
    assert not can(renewing, "billing.pay_membership")
    Subscription.objects.filter(user=renewing).update(cancel_at_period_end=True)
    assert can(renewing, "billing.pay_membership")


def test_billing_page_is_the_members_own(client, make_user, fake_stripe):
    member = make_user("full")
    Subscription.objects.create(user=member, status="active", stripe_customer_id="cus_7")
    enrol_totp(member)
    client.force_login(member)
    assert client.get("/billing/").status_code == 200
    assert client.post("/billing/manage/")["Location"] == "https://billing.stripe.test/cus_7"
    assert not can(make_user("admin"), "billing.view", member)
