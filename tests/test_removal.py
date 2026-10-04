"""Removal, leaving and reinstatement (rule 66)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client

from accounts import removal
from accounts.models import User, UserSession
from audit.models import AuditEntry
from billing import stripe_api
from billing.models import Charge, Subscription
from boards import services
from moderation import services as moderation
from moderation.models import ModerationAction
from sponsorship import transfers
from sponsorship.models import Sponsorship, SponsorshipTransfer
from tests.factories import enrol_totp, make_thread, sponsor


def signed_in(user):
    enrol_totp(user)
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def stripe(monkeypatch):
    calls = {"cancel": [], "refund": []}
    monkeypatch.setattr(stripe_api, "cancel_subscription", calls["cancel"].append)
    monkeypatch.setattr(stripe_api, "refund", calls["refund"].append)
    return calls


@pytest.fixture
def family(make_user):
    """A Full member with a sponsor of their own and a Provisional sponsee."""
    top, member, sponsee = make_user("tenured"), make_user("full"), make_user("provisional")
    sponsor(top, member)
    sponsor(member, sponsee)
    return top, member, sponsee


def test_admin_removes_a_member(family, make_user, stripe, general):
    top, member, sponsee = family
    Subscription.objects.create(user=member, stripe_subscription_id="sub_1", status="active")
    post = services.reply(member, make_thread(general, member), "my words stay")
    client = signed_in(member)
    admin = make_user("admin")
    with pytest.raises(ValidationError):
        removal.remove(admin, member, " ")
    removal.remove(admin, member, "Asked to go after a dispute")
    member.refresh_from_db()
    assert (member.status, member.removed_by, member.removal_reason) == ("removed", admin, "Asked to go after a dispute")
    assert member.removed_at is not None and not member.is_active
    assert client.get("/").status_code == 302
    assert set(UserSession.objects.filter(user=member).values_list("revoke_reason", flat=True)) == {"signed_out"}
    assert stripe["cancel"] == ["sub_1"] and stripe["refund"] == []
    assert Sponsorship.objects.get(member=member, sponsor=top).end_reason == "member_removed"
    assert SponsorshipTransfer.objects.get(member=sponsee).cause == "sponsor_left"
    assert not ModerationAction.objects.filter(target_user=member).exists()
    assert AuditEntry.objects.filter(action="member.remove").exists()
    post.refresh_from_db()
    assert post.deleted_at is None and post.author == member


@pytest.mark.parametrize("actor_role,target_role,allowed", [
    ("moderator", "full", False), ("admin", "full", True), ("admin", "admin", False), ("owner", "admin", True),
])
def test_who_removes_whom(make_user, actor_role, target_role, allowed):
    actor, target = make_user(actor_role), make_user(target_role)
    if allowed:
        removal.remove(actor, target, "reason")
    else:
        with pytest.raises(PermissionDenied):
            removal.remove(actor, target, "reason")


def test_profile_shows_membership_ended_but_ended_invitations_stay_hidden(family, make_user, client):
    _, member, _ = family
    removal.remove(make_user("admin"), member, "reason")
    viewer = signed_in(make_user("full"))
    page = viewer.get(f"/members/{member.slug}/")
    assert page.status_code == 200 and b"Membership ended" in page.content
    never_joined = make_user("full", status=User.Status.REMOVED)
    assert viewer.get(f"/members/{never_joined.slug}/").status_code == 404


def test_a_member_leaves(family, stripe):
    top, member, sponsee = family
    client = signed_in(member)
    assert b"Leave the forum" in client.get("/leave/").content
    response = client.post("/leave/", {"confirm": "leave", "reason": "moving on"})
    assert b"Your membership has ended" in response.content and response["Clear-Site-Data"]
    member.refresh_from_db()
    assert member.status == "removed" and member.removed_by is None and member.removal_reason == "moving on"
    assert Sponsorship.objects.get(member=member, sponsor=top).end_reason == "member_left"
    assert transfers.awaiting_sponsor(sponsee)
    assert client.get("/").status_code == 302


def test_staff_step_down_before_leaving(make_user):
    mod = make_user("moderator")
    with pytest.raises(PermissionDenied):
        removal.leave(mod)


def test_removing_a_waiting_sponsee_closes_their_transfer(family, make_user):
    top, member, _ = family
    moderation.initiate_action(make_user("admin"), top, "ban", internal_reason="abuse")
    removal.remove(make_user("admin"), member, "reason")
    transfer = SponsorshipTransfer.objects.get(member=member)
    assert (transfer.status, transfer.decision) == ("decided", "removed")


def test_reinstating_restores_the_account_as_it_was(family, make_user):
    top, member, sponsee = family
    admin = make_user("admin")
    removal.remove(admin, member, "mistake")
    with pytest.raises(PermissionDenied):
        removal.reinstate(make_user("moderator"), member, "x")
    with pytest.raises(ValidationError):
        removal.reinstate(admin, member, "")
    removal.reinstate(admin, member, "Removed in error")
    member.refresh_from_db()
    assert member.status == "active" and member.removed_at is None
    restored = Sponsorship.objects.get(member=member, ended_at__isnull=True)
    assert restored.sponsor == top and restored.previous.end_reason == "member_removed"
    # Their own sponsee's transfer resumes with them.
    assert SponsorshipTransfer.objects.get(member=sponsee).status == "resumed"
    assert signed_in(member).get("/").status_code == 200


def test_reinstated_member_waits_if_their_sponsor_cannot_sponsor(family, make_user):
    top, member, _ = family
    admin = make_user("admin")
    removal.remove(admin, member, "reason")
    moderation.initiate_action(admin, top, "ban", internal_reason="abuse")
    removal.reinstate(admin, member, "back")
    assert SponsorshipTransfer.objects.get(member=member, status="open").cause == "sponsor_banned"


def test_only_removed_members_are_reinstated(make_user):
    with pytest.raises(PermissionDenied):
        removal.reinstate(make_user("admin"), make_user("full"), "x")


def test_refund_only_for_removed_members(family, make_user, stripe):
    _, member, _ = family
    charge = Charge.objects.create(user=member, kind=Charge.Kind.SUBSCRIPTION, stripe_payment_intent_id="pi_9",
                                   amount_cents=2500, currency="usd", status="succeeded")
    admin = make_user("admin")
    with pytest.raises(PermissionDenied):
        removal.refund(admin, charge)
    removal.remove(admin, member, "reason")
    with pytest.raises(PermissionDenied):
        removal.refund(make_user("moderator"), charge)
    removal.refund(admin, charge)
    assert stripe["refund"] == ["pi_9"] and Charge.objects.get(pk=charge.pk).status == "refunded"


def test_remove_and_reinstate_from_the_staff_view(family, make_user):
    _, member, _ = family
    client = signed_in(make_user("admin"))
    assert b"End their membership" in client.get(f"/staff/members/{member.slug}/").content
    client.post(f"/staff/members/{member.slug}/membership/remove/", {"reason": "asked"})
    member.refresh_from_db()
    assert member.status == "removed"
    assert b"Reinstate member" in client.get(f"/staff/members/{member.slug}/").content
    client.post(f"/staff/members/{member.slug}/membership/reinstate/", {"reason": "back"})
    member.refresh_from_db()
    assert member.status == "active"
