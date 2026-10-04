"""Build step 5 part 3: ban payments with the doubling fee, and the Permanent Ban (rules 45 and 46)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from audit.models import AuditEntry
from billing import bans, stripe_api, webhooks
from billing.models import Charge
from core.permissions import can
from moderation import permanent
from moderation import services as moderation
from moderation.models import ModerationAction, PermanentBanRecord
from tests.factories import accepted, enrol_totp

_ids = iter(range(1, 10**6))


def _ban(member, admin):
    return moderation.initiate_action(admin, member, "ban", internal_reason="abuse", public_summary="Abuse")


def _pay(member, ban, amount):
    """A completed Checkout session for the ban, shaped like Stripe's."""
    return webhooks.process({"id": f"evt_ban_{next(_ids)}", "type": "checkout.session.completed", "data": {"object": {
        "id": f"cs_{next(_ids)}", "mode": "payment", "payment_status": "paid", "amount_total": amount,
        "currency": "usd", "payment_intent": f"pi_{next(_ids)}",
        "metadata": {"purpose": "ban", "user": str(member.pk), "ban": str(ban.pk), "amount": str(amount)},
    }}})


# --- ban payments --------------------------------------------------------------------------


def test_fee_doubles_with_each_paid_off_ban(make_user):
    member, admin = make_user("full"), make_user("admin")
    fees = []
    for _ in range(3):
        ban = _ban(member, admin)
        fees.append(bans.fee_cents(member))
        _pay(member, ban, fees[-1])
    assert fees == [1000, 2000, 4000]


def test_payment_lifts_the_ban_only(make_user, general):
    member, admin = make_user("full"), make_user("admin")
    moderation.initiate_action(admin, member, "probation", internal_reason="x")
    ban = _ban(member, admin)
    assert not can(member, "subforum.read", general)
    _pay(member, ban, 1000)
    ban.refresh_from_db()
    assert ban.status == "reversed"
    assert can(member, "subforum.read", general)                       # the ban is gone
    assert ModerationAction.objects.in_force().filter(target_user=member, kind="probation").exists()
    entry = AuditEntry.objects.get(action="moderation.lift_ban_paid")
    assert entry.payload["amount_cents"] == 1000
    charge = Charge.objects.get(user=member)
    assert (charge.kind, charge.related_action) == ("ban_reversal", ban)


def test_paying_twice_for_the_same_session_changes_nothing(make_user):
    member, admin = make_user("full"), make_user("admin")
    ban = _ban(member, admin)
    session_event = {"id": "evt_once", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_once", "mode": "payment", "payment_status": "paid", "amount_total": 1000, "currency": "usd",
        "payment_intent": "pi_once", "metadata": {"purpose": "ban", "user": str(member.pk), "ban": str(ban.pk)},
    }}}
    webhooks.process(session_event)
    webhooks.process(session_event)
    assert Charge.objects.filter(user=member).count() == 1


def test_banned_member_reaches_the_ban_payment_page(client, make_user, monkeypatch):
    member, admin = make_user("full"), make_user("admin")
    ban = _ban(member, admin)
    calls = []
    monkeypatch.setattr(stripe_api, "create_checkout_session",
                        lambda **p: calls.append(p) or {"url": "https://checkout.stripe.test/ban"})
    enrol_totp(member)
    client.force_login(member)
    assert b"$10.00" in client.get("/billing/ban/").content
    assert client.post("/billing/ban/")["Location"] == "https://checkout.stripe.test/ban"
    [params] = calls
    assert params["mode"] == "payment"
    assert params["line_items"][0]["price_data"]["unit_amount"] == 1000
    assert params["metadata"]["ban"] == str(ban.pk)
    assert client.get("/billing/").status_code == 403  # only the ban page is open to them


# --- the Permanent Ban ---------------------------------------------------------------------


def test_only_an_owner_imposes_one(make_user):
    member = make_user("full")
    with pytest.raises(PermissionDenied):
        permanent.impose(make_user("admin"), member, "repeated harassment")
    action = permanent.impose(make_user("owner"), member, "repeated harassment", "Harassment")
    assert action.status == "active"


def test_it_cannot_be_paid(make_user):
    member = make_user("full")
    action = permanent.impose(make_user("owner"), member, "x")
    assert not can(member, "billing.pay_ban", action)
    assert bans.active_ban(member) is None


def test_it_blocks_sign_in_and_ends_sessions(client, make_user):
    from allauth.account.models import EmailAddress

    from tests.factories import PASSWORD

    member = make_user("full")
    member.set_password(PASSWORD)
    member.save()
    EmailAddress.objects.create(user=member, email=member.email, verified=True, primary=True)
    enrol_totp(member)
    client.force_login(member)
    permanent.impose(make_user("owner"), member, "x")
    response = client.get("/")
    assert response.status_code == 403 and b"permanently banned" in response.content
    assert client.get("/")["Location"].startswith("/accounts/login/")  # the session is gone
    response = client.post("/accounts/login/", {"login": member.email, "password": PASSWORD})
    assert response.status_code == 403 and b"permanently banned" in response.content


def test_the_list_holds_emails_and_real_name_encrypted(make_user):
    from django.db import connection

    from accounts.models import IdentityRecord

    member = make_user("full")
    IdentityRecord.objects.create(user=member, real_name="Mallory Example")
    action = permanent.impose(make_user("owner"), member, "x")
    record = PermanentBanRecord.objects.get(action=action)
    assert member.email in record.emails and record.real_name == "Mallory Example"
    with connection.cursor() as cursor:
        cursor.execute("SELECT emails, real_name FROM moderation_permanentbanrecord WHERE id=%s", [record.pk])
        stored = cursor.fetchone()
    assert member.email not in stored[0] and "Mallory" not in stored[1]


def test_invitations_are_checked_and_matches_shown_not_acted_on(client, make_user, owner):
    from accounts.models import IdentityRecord

    banned = make_user("full")
    IdentityRecord.objects.create(user=banned, real_name="Mallory Example")
    permanent.impose(owner, banned, "x")
    invitation = accepted(make_user("full"))
    IdentityRecord.objects.filter(user=invitation.invitee).update(real_name="mallory  example")
    assert [r.action.target_user for r in permanent.matches(invitation.invitee_email, "mallory  example")] == [banned]
    admin = make_user("admin")
    enrol_totp(admin)
    client.force_login(admin)
    page = client.get("/staff/onboarding/").content.decode()
    assert "Matches the permanent-ban list" in page
    invitation.refresh_from_db()
    assert invitation.status == "accepted"  # nothing happened on its own


def test_only_an_owner_annuls_with_a_reason(make_user, owner):
    member = make_user("full")
    action = permanent.impose(owner, member, "x")
    with pytest.raises(PermissionDenied):
        permanent.annul(make_user("admin"), action, "mistaken identity")
    with pytest.raises(ValidationError):
        permanent.annul(owner, action, " ")
    permanent.annul(owner, action, "Mistaken identity")
    action.refresh_from_db()
    assert action.status == "annulled"
    assert PermanentBanRecord.objects.get(action=action).annulled_at is not None
    assert not permanent.is_permanently_banned(member)
    assert permanent.matches(member.email) == []
    assert AuditEntry.objects.get(action="moderation.annul").payload["reason"] == "Mistaken identity"
