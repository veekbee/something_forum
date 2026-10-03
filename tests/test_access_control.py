"""No unauthenticated surface and no forum without TOTP (design rule 9)."""

import pytest

from tests.factories import enrol_totp


def test_anonymous_get_redirects_to_login(client, seeded):
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.parametrize("path", ["/staff/admin/", "/accounts/signup/", "/accounts/email/", "/media/x.png"])
def test_anonymous_cannot_reach_other_pages(client, seeded, path):
    response = client.get(path)
    assert response.status_code == 302 and response["Location"].startswith("/accounts/login/")


def test_anonymous_non_get_and_htmx_get_are_401(client, seeded):
    assert client.post("/").status_code == 401
    assert client.get("/", HTTP_HX_REQUEST="true").status_code == 401


def test_login_page_is_public(client, seeded):
    assert client.get("/accounts/login/").status_code == 200


def test_password_reset_goes_only_to_verified_addresses(client, make_user, mailoutbox):
    from allauth.account.models import EmailAddress

    verified, unverified = make_user("full"), make_user("full")
    EmailAddress.objects.create(user=verified, email=verified.email, verified=True, primary=True)
    EmailAddress.objects.create(user=unverified, email=unverified.email, verified=False, primary=True)
    assert client.get("/accounts/password/reset/").status_code == 200

    client.post("/accounts/password/reset/", {"email": unverified.email})
    assert not any(unverified.email in m.to for m in mailoutbox if "/password/reset/key/" in m.body)

    client.post("/accounts/password/reset/", {"email": verified.email})
    reset = [m for m in mailoutbox if verified.email in m.to and "/password/reset/key/" in m.body]
    assert len(reset) == 1


def test_robots_and_noindex(client, seeded):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.content == b"User-agent: *\nDisallow: /\n"
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert client.get("/accounts/login/")["X-Robots-Tag"] == "noindex, nofollow"


def test_signed_in_without_totp_is_sent_to_enrolment(client, make_user):
    member = make_user("full")
    client.force_login(member)
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == "/accounts/2fa/totp/activate/"
    assert client.post("/").status_code == 403


def test_password_login_then_enrolment_without_totp(client, make_user):
    from allauth.account.models import EmailAddress

    member = make_user("full")
    EmailAddress.objects.create(user=member, email=member.email, verified=True, primary=True)
    response = client.post("/accounts/login/", {"login": member.email, "password": "not-a-real-password"})
    assert response.status_code == 302
    assert client.get("/").status_code == 302
    assert client.get("/accounts/2fa/totp/activate/").status_code == 200


def test_signed_in_with_totp_reaches_the_forum(client, make_user):
    member = make_user("full")
    enrol_totp(member)
    client.force_login(member)
    assert client.get("/").status_code == 200


def test_logout_clears_site_data(client, make_user):
    member = make_user("full")
    enrol_totp(member)
    client.force_login(member)
    response = client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response["Clear-Site-Data"] == '"cache", "storage"'
    assert client.get("/")["Location"].startswith("/accounts/login/")


def test_logout_confirmation_page_does_not_clear(client, make_user):
    member = make_user("full")
    enrol_totp(member)
    client.force_login(member)
    response = client.get("/accounts/logout/")
    assert response.status_code == 200
    assert "Clear-Site-Data" not in response


def test_staff_admin_is_for_admins_and_owners(client, make_user, owner):
    full = make_user("full")
    enrol_totp(full)
    client.force_login(full)
    assert client.get("/staff/admin/").status_code in (302, 403)

    enrol_totp(owner)
    client.force_login(owner)
    response = client.get("/staff/admin/")
    assert response.status_code == 200
    assert client.get("/staff/admin/audit/auditentry/").status_code == 200
    assert client.get("/staff/admin/core/sitesetting/add/").status_code == 403


def test_self_signup_is_closed(client, seeded, make_user):
    member = make_user("full")
    enrol_totp(member)
    client.force_login(member)
    response = client.get("/accounts/signup/")
    assert b"closed" in response.content.lower() or response.status_code in (302, 403)
