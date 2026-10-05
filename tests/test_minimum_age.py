"""Members must be 18 or older (rule 83, decided 5 Oct 2026)."""

import pytest
from django.core.exceptions import ValidationError

from sponsorship import onboarding
from sponsorship.models import Invitation
from tests.factories import PASSWORD, enrol_totp, invite


def _url(token):
    return f"https://forum.example.test/invitations/accept/{token}/"


def test_the_sponsor_confirms_before_sending(make_user):
    sponsor = make_user("tenured")
    with pytest.raises(ValidationError, match="18 or older"):
        onboarding.send_invitation(sponsor, "young@example.test", "Neighbour", accept_url=_url)
    assert not Invitation.objects.filter(invitee_email="young@example.test").exists()
    invitation, _ = invite(sponsor)
    assert invitation.sponsor_confirmed_adult and not invitation.invitee_confirmed_adult


def test_the_invitee_confirms_before_accepting(make_user):
    invitation, token = invite(make_user("tenured"))
    with pytest.raises(ValidationError, match="18 or older"):
        onboarding.accept(token, "New Person", PASSWORD)
    invitation.refresh_from_db()
    assert invitation.status == "pending" and invitation.invitee is None
    onboarding.accept(token, "New Person", PASSWORD, confirmed_adult=True)
    invitation.refresh_from_db()
    assert invitation.invitee_confirmed_adult and invitation.status in ("accepted", "waitlisted")


def test_both_forms_require_the_box(client, make_user):
    sponsor = make_user("tenured")
    enrol_totp(sponsor)
    client.force_login(sponsor)
    page = client.post("/invitations/", {"invitee_email": "a@example.test", "vouching_notes": "Friend"})
    assert b"They are 18 or older" in page.content
    assert not Invitation.objects.filter(invitee_email="a@example.test").exists()
    client.logout()
    _, token = invite(sponsor)
    response = client.post(f"/invitations/accept/{token}/", {"display_name": "New Person", "password1": PASSWORD,
                                                              "password2": PASSWORD})
    assert response.status_code == 200 and b"I am 18 or older" in response.content
    assert not Invitation.objects.get(token_hash__isnull=False, invitee_email__startswith="invitee",
                                      sponsor=sponsor).invitee_id


def test_the_draft_member_agreement_says_so(client, db):
    assert b"18 or older" in client.get("/legal/member-agreement/").content
