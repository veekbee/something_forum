"""Resetting a lost second factor (rule 63)."""

import io

import pytest
from allauth.mfa.models import Authenticator
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import CommandError, call_command
from django.test import Client

from accounts import factor_reset
from accounts.models import UserSession
from audit.models import AuditEntry
from core.models import Notification
from core.notifications import ACCOUNT_KINDS
from tests.factories import enrol_totp, grant, sponsor


@pytest.fixture
def member(make_user):
    user = make_user("provisional")
    sponsor(make_user("full"), user)
    enrol_totp(user)
    return user


def _client(user):
    client = Client()
    client.force_login(user)
    return client


def test_admin_resets_after_consulting_the_sponsor(member, make_user):
    phone, laptop = _client(member), _client(member)
    assert phone.get("/").status_code == 200
    admin = make_user("admin")
    factor_reset.reset(admin, member, factor_reset.SPONSOR)
    assert not Authenticator.objects.filter(user=member).exists()
    assert set(UserSession.objects.filter(user=member).values_list("revoke_reason", flat=True)) == {"factor_reset"}
    assert phone.get("/").status_code == 302 and laptop.get("/").status_code == 302
    entry = AuditEntry.objects.get(action="account.factor_reset")
    assert entry.actor == admin and entry.payload["method"] == "sponsor_consulted"
    assert Notification.objects.filter(recipient=member, kind="account.factor_reset").exists()
    assert "account.factor_reset" in ACCOUNT_KINDS


def test_next_sign_in_must_set_up_a_new_authenticator(member, make_user):
    factor_reset.reset(make_user("admin"), member, factor_reset.SPONSOR)
    response = _client(member).get("/")
    assert response.status_code == 302 and "/2fa/totp/activate/" in response["Location"]


@pytest.mark.parametrize("method,note", [("", ""), ("guess", ""), ("other", "phoned them")])
def test_how_identity_was_confirmed_is_required(member, make_user, method, note):
    """Another way is refused while the sponsor can be consulted."""
    with pytest.raises(ValidationError):
        factor_reset.reset(make_user("admin"), member, method, note)
    assert Authenticator.objects.filter(user=member).exists()


def test_another_way_when_the_sponsor_is_staff_or_gone(make_user):
    admin = make_user("admin")
    with_staff_sponsor = make_user("provisional")
    sponsor(make_user("moderator"), with_staff_sponsor)
    tenured = make_user("tenured")  # no active sponsor
    for target in (with_staff_sponsor, tenured):
        enrol_totp(target)
        with pytest.raises(ValidationError):
            factor_reset.reset(admin, target, factor_reset.OTHER, "")
        factor_reset.reset(admin, target, factor_reset.OTHER, "video call, matched their invitation details")
        assert not Authenticator.objects.filter(user=target).exists()


@pytest.mark.parametrize("actor_role,target_role,allowed", [
    ("moderator", "full", False), ("admin", "full", True), ("admin", "moderator", True),
    ("admin", "admin", False), ("owner", "admin", True), ("admin", "owner", False), ("owner", "owner", True),
])
def test_rank_rules(make_user, actor_role, target_role, allowed):
    actor, target = make_user(actor_role), make_user(target_role)
    enrol_totp(target)
    if allowed:
        factor_reset.reset(actor, target, factor_reset.OTHER, "confirmed in person")
    else:
        with pytest.raises(PermissionDenied):
            factor_reset.reset(actor, target, factor_reset.OTHER, "confirmed in person")


def test_nobody_resets_their_own(make_user):
    owner = make_user("owner")
    with pytest.raises(PermissionDenied):
        factor_reset.reset(owner, owner, factor_reset.OTHER, "me")


def test_reset_from_the_staff_view(member, make_user):
    admin = make_user("admin")
    enrol_totp(admin)
    client = _client(admin)
    assert b"Reset their second factor" in client.get(f"/staff/members/{member.slug}/").content
    assert b"Reset their second factor" not in client.get(f"/staff/members/{make_user('admin').slug}/").content
    client.post(f"/staff/members/{member.slug}/reset-factor/", {"method": "sponsor_consulted"})
    assert not Authenticator.objects.filter(user=member).exists()


# --- the server command for a sole Owner -------------------------------------------------------


def test_command_resets_the_sole_owner(owner):
    enrol_totp(owner)
    call_command("reset_second_factor", owner.email, note="lost phone", stdout=io.StringIO())
    assert not Authenticator.objects.filter(user=owner).exists()
    entry = AuditEntry.objects.get(action="account.factor_reset")
    assert entry.actor is None and entry.payload["method"] == "server_command"


def test_command_refuses_when_another_owner_exists(owner, make_user):
    make_user("owner")
    enrol_totp(owner)
    with pytest.raises(CommandError):
        call_command("reset_second_factor", owner.email, note="lost phone", stdout=io.StringIO())
    assert Authenticator.objects.filter(user=owner).exists()


def test_command_refuses_anyone_but_the_owner(owner, make_user):
    admin = make_user("admin")
    grant(admin, "admin")
    enrol_totp(admin)
    with pytest.raises(CommandError):
        call_command("reset_second_factor", admin.email, note="x", stdout=io.StringIO())
    with pytest.raises(CommandError):
        call_command("reset_second_factor", owner.email, note=" ", stdout=io.StringIO())
