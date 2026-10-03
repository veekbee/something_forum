"""Milestone 2 onboarding: every transition in the onboarding table, the slot rules, the jobs, the
comp path and the pages (docs/DESIGN.md, Milestone 2 definition of done)."""

import re
from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts import roles
from accounts.models import IdentityRecord, User
from audit.models import AuditEntry
from billing.models import Subscription
from core.models import Notification
from core.permissions import can
from core.services import set_site_setting
from sponsorship import capacity, onboarding
from sponsorship.models import Invitation, Sponsorship
from tests.factories import PASSWORD, accepted, enrol_totp, invite, sponsor

S = Invitation.Status


def _age(invitation, **delta):
    Invitation.objects.filter(pk=invitation.pk).update(created_at=timezone.now() - timedelta(**delta))
    invitation.refresh_from_db()
    return invitation


def _actions(invitation):
    return list(
        AuditEntry.objects.filter(target_type="sponsorship.invitation", target_id=str(invitation.pk))
        .order_by("pk").values_list("action", flat=True)
    )


# --- sending ---------------------------------------------------------------------------------


def test_send_stores_only_the_token_hash_and_audits(make_user):
    from sponsorship.services import hash_token

    sponsor_user = make_user("full")
    invitation, token = invite(sponsor_user)
    assert invitation.status == S.PENDING and invitation.token_hash == hash_token(token)
    assert _actions(invitation) == ["invitation.send"]


def test_send_emails_the_link(make_user, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        _, token = invite(make_user("full"), email="friend@example.test")
    assert mailoutbox[0].to == ["friend@example.test"]
    assert f"/invitations/accept/{token}/" in mailoutbox[0].body


@pytest.mark.parametrize("role", ["guest", "provisional"])
def test_only_full_and_above_send(make_user, role):
    with pytest.raises(PermissionDenied):
        invite(make_user(role))


def test_refuses_existing_accounts_and_duplicate_invitations(make_user):
    sponsor_user = make_user("tenured")
    with pytest.raises(ValidationError):
        invite(sponsor_user, email=make_user("full").email)
    invite(sponsor_user, email="twice@example.test")
    with pytest.raises(ValidationError):
        invite(make_user("full"), email="TWICE@example.test")


def test_admin_declined_address_cannot_be_invited_again_yet(make_user):
    invitation = accepted(make_user("full"), email="declined@example.test")
    onboarding.decline(make_user("admin"), invitation, reason="Unknown to anyone")
    with pytest.raises(ValidationError):
        invite(make_user("tenured"), email="declined@example.test")


# --- accepting -------------------------------------------------------------------------------


def test_accept_creates_an_invited_account_with_verified_email(make_user):
    sponsor_user = make_user("full")
    invitation, token = invite(sponsor_user, email="new@example.test")
    invitation = onboarding.accept(token, "New Person", PASSWORD)
    user = invitation.invitee
    assert user.status == User.Status.INVITED and user.check_password(PASSWORD)
    assert EmailAddress.objects.get(user=user).verified
    identity = IdentityRecord.objects.get(user=user)
    assert identity.email_verified_at is not None and identity.submitted_at is None
    assert identity.vouching_notes == invitation.vouching_notes
    assert invitation.status == S.ACCEPTED and invitation.accepted_at is not None
    assert roles.trust_role(user) is None
    assert Notification.objects.get(recipient=sponsor_user).kind == "invitation.accepted"
    entry = AuditEntry.objects.get(action="invitation.accept")
    assert entry.actor is None and entry.payload["invitee"] == user.pk


def test_link_works_once(make_user):
    _, token = invite(make_user("full"))
    onboarding.accept(token, "New Person", PASSWORD)
    with pytest.raises(PermissionDenied):
        onboarding.accept(token, "Someone Else", PASSWORD)


def test_expired_link_is_refused(make_user):
    invitation, token = invite(make_user("full"))
    _age(invitation, days=15)
    assert onboarding.invitation_for_token(token) is None
    with pytest.raises(PermissionDenied):
        onboarding.accept(token, "New Person", PASSWORD)


def test_weak_password_creates_nothing(make_user):
    invitation, token = invite(make_user("full"))
    with pytest.raises(ValidationError):
        onboarding.accept(token, "New Person", "password")
    invitation.refresh_from_db()
    assert invitation.status == S.PENDING and not User.objects.filter(status=User.Status.INVITED).exists()


def test_identity_needs_totp_first_and_is_submitted_once(make_user):
    invitation = accepted(make_user("full"), totp=False)
    with pytest.raises(PermissionDenied):
        onboarding.submit_identity(invitation.invitee, "Real Name")
    enrol_totp(invitation.invitee)
    onboarding.submit_identity(invitation.invitee, "Real Name")
    identity = IdentityRecord.objects.get(user=invitation.invitee)
    assert identity.real_name == "Real Name" and identity.submitted_at is not None
    with pytest.raises(PermissionDenied):
        onboarding.submit_identity(invitation.invitee, "Another Name")


# --- slots -----------------------------------------------------------------------------------


def test_send_order_holds_a_later_invitee_who_accepts_first_waits(make_user):
    sponsor_user = make_user("full")  # cap 1
    first, first_token = invite(sponsor_user)
    second, second_token = invite(sponsor_user)
    assert onboarding.accept(second_token, "Second", PASSWORD).status == S.WAITLISTED
    assert onboarding.accept(first_token, "First", PASSWORD).status == S.ACCEPTED


def test_freed_slot_goes_to_the_earliest_sent_invitation(make_user):
    sponsor_user = make_user("full")
    first, _ = invite(sponsor_user)
    second = accepted(sponsor_user)
    third = accepted(sponsor_user)
    assert (second.status, third.status) == (S.WAITLISTED, S.WAITLISTED)
    onboarding.rescind(sponsor_user, first)
    second.refresh_from_db()
    third.refresh_from_db()
    assert (second.status, third.status) == (S.ACCEPTED, S.WAITLISTED)
    assert "invitation.slot_assigned" in _actions(second)


def test_active_sponsorships_take_slots_first(make_user):
    sponsor_user = make_user("full")
    sponsor(sponsor_user, make_user("provisional"))
    assert capacity.at_capacity(sponsor_user)
    assert accepted(sponsor_user).status == S.WAITLISTED


def test_admins_have_a_slot_for_every_invitation(make_user):
    admin = make_user("admin")
    assert all(accepted(admin).status == S.ACCEPTED for _ in range(4))


def test_tenured_promotion_frees_a_slot(make_user):
    from sponsorship import promotions

    sponsor_user = make_user("full")
    member = make_user("full", granted_at=timezone.now() - timedelta(days=91))
    sponsor(sponsor_user, member)
    waiting = accepted(sponsor_user)
    assert waiting.status == S.WAITLISTED
    promotions.promote_to_tenured(make_user("admin"), member)
    waiting.refresh_from_db()
    assert waiting.status == S.ACCEPTED


def test_raising_a_cap_frees_slots(make_user, owner):
    sponsor_user = make_user("full")
    invite(sponsor_user)
    waiting = accepted(sponsor_user)
    set_site_setting(owner, "sponsorship.cap.full", 2)
    waiting.refresh_from_db()
    assert waiting.status == S.ACCEPTED


# --- review ----------------------------------------------------------------------------------


def test_approval_makes_a_guest_with_sponsorship(make_user, lobby):
    sponsor_user, admin = make_user("tenured"), make_user("admin")
    invitation = accepted(sponsor_user)
    onboarding.approve(admin, invitation)
    invitation.refresh_from_db()
    member = invitation.invitee
    assert invitation.status == S.APPROVED and invitation.decided_by == admin
    assert member.status == User.Status.GUEST and roles.trust_role(member).name == "guest"
    sponsorship = Sponsorship.objects.get(member=member)
    assert sponsorship.sponsor == sponsor_user and sponsorship.is_original and sponsorship.sponsor_role.name == "tenured"
    identity = IdentityRecord.objects.get(user=member)
    assert identity.reviewed_by == admin and identity.reviewed_at is not None
    assert "invitation.approve" in _actions(invitation)
    assert Notification.objects.filter(recipient=sponsor_user, kind="invitation.approved").exists()
    assert can(member, "subforum.read", lobby)


def test_approving_a_waitlisted_invitation_is_audited_as_over_cap(make_user):
    sponsor_user = make_user("full")
    invite(sponsor_user)
    waiting = accepted(sponsor_user)
    onboarding.approve(make_user("owner"), waiting)
    assert "invitation.approve_over_cap" in _actions(waiting)
    assert Sponsorship.objects.filter(sponsor=sponsor_user, ended_at__isnull=True).count() == 1


@pytest.mark.parametrize("role", ["moderator", "tenured", "full"])
def test_only_admin_or_owner_reviews(make_user, role):
    invitation = accepted(make_user("full"))
    with pytest.raises(PermissionDenied):
        onboarding.approve(make_user(role), invitation)
    with pytest.raises(PermissionDenied):
        onboarding.decline(make_user(role), invitation)


def test_no_review_before_details_are_submitted(make_user):
    invitation = accepted(make_user("full"), submit=False)
    with pytest.raises(PermissionDenied):
        onboarding.approve(make_user("admin"), invitation)
    assert invitation not in onboarding.review_queue()


def test_queue_is_longest_waiting_first(make_user):
    later = accepted(make_user("full"))
    earlier = accepted(make_user("full"))
    IdentityRecord.objects.filter(user=earlier.invitee).update(submitted_at=timezone.now() - timedelta(days=3))
    assert list(onboarding.review_queue()) == [earlier, later]


def test_decline_ends_the_account_and_tells_the_sponsor(make_user):
    sponsor_user = make_user("full")
    invitation = accepted(sponsor_user)
    onboarding.decline(make_user("admin"), invitation, reason="Nobody knows them")
    invitation.refresh_from_db()
    assert invitation.status == S.DECLINED
    assert invitation.invitee.status == User.Status.REMOVED and not invitation.invitee.is_active
    assert Notification.objects.filter(recipient=sponsor_user, kind="invitation.declined").exists()
    with pytest.raises(PermissionDenied):
        onboarding.approve(make_user("admin"), invitation)


def test_invitee_can_decline_before_approval_only(make_user):
    sponsor_user = make_user("full")
    pending, token = invite(sponsor_user)
    onboarding.invitee_decline(pending, token=token)
    pending.refresh_from_db()
    assert pending.status == S.INVITEE_DECLINED and pending.invitee is None

    waiting = accepted(sponsor_user)
    onboarding.invitee_decline(waiting, invitee=waiting.invitee)
    waiting.refresh_from_db()
    assert waiting.status == S.INVITEE_DECLINED and waiting.invitee.status == User.Status.REMOVED
    assert Notification.objects.filter(recipient=sponsor_user, kind="invitation.invitee_declined").count() == 2

    approved = accepted(make_user("admin"))
    onboarding.approve(make_user("admin"), approved)
    with pytest.raises(PermissionDenied):
        onboarding.invitee_decline(approved, invitee=approved.invitee)


def test_sponsor_rescinds_before_approval_only(make_user):
    sponsor_user = make_user("tenured")
    invitation = accepted(sponsor_user)
    with pytest.raises(PermissionDenied):
        onboarding.rescind(make_user("admin"), invitation)
    onboarding.rescind(sponsor_user, invitation)
    invitation.refresh_from_db()
    assert invitation.status == S.RESCINDED and invitation.invitee.status == User.Status.REMOVED

    approved = accepted(sponsor_user)
    onboarding.approve(make_user("admin"), approved)
    with pytest.raises(PermissionDenied):
        onboarding.rescind(sponsor_user, approved)


# --- jobs ------------------------------------------------------------------------------------


def test_expiry_job_expires_only_old_pending_invitations(make_user):
    sponsor_user = make_user("admin")
    old_pending, _ = invite(sponsor_user)
    _age(old_pending, days=15)
    fresh_pending, _ = invite(sponsor_user)
    old_accepted = _age(accepted(sponsor_user), days=40)
    assert onboarding.expire_pending() == 1
    statuses = {inv.pk: inv.status for inv in Invitation.objects.all()}
    assert statuses[old_pending.pk] == S.EXPIRED
    assert statuses[fresh_pending.pk] == S.PENDING and statuses[old_accepted.pk] == S.ACCEPTED


def test_expiry_frees_a_slot(make_user):
    sponsor_user = make_user("full")
    old, _ = invite(sponsor_user)
    _age(old, days=15)
    waiting = accepted(sponsor_user)
    onboarding.expire_pending()
    waiting.refresh_from_db()
    assert waiting.status == S.ACCEPTED


def test_deletion_job(make_user):
    sponsor_user = make_user("admin")
    old_declined = accepted(sponsor_user)
    onboarding.decline(make_user("admin"), old_declined)
    recent_declined = accepted(sponsor_user)
    onboarding.decline(make_user("admin"), recent_declined)
    approved = accepted(sponsor_user)
    onboarding.approve(make_user("admin"), approved)
    Invitation.objects.filter(pk=old_declined.pk).update(decided_at=timezone.now() - timedelta(days=31))
    old_user_pk = old_declined.invitee_id
    audit_before = set(AuditEntry.objects.values_list("pk", flat=True))

    assert onboarding.delete_ended_accounts() == 1

    old_declined.refresh_from_db()
    assert old_declined.invitee is None and old_declined.status == S.DECLINED
    assert not User.objects.filter(pk=old_user_pk).exists()
    assert not IdentityRecord.objects.filter(user_id=old_user_pk).exists()
    assert audit_before <= set(AuditEntry.objects.values_list("pk", flat=True))
    assert AuditEntry.objects.filter(action="invitation.account_deleted", payload__invitee=old_user_pk).exists()
    recent_declined.refresh_from_db()
    assert recent_declined.invitee is not None
    assert User.objects.filter(pk=approved.invitee_id).exists()


def test_job_commands_run(make_user):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("expire_invitations", stdout=out)
    call_command("delete_ended_accounts", stdout=out)
    assert "Expired 0" in out.getvalue() and "Deleted 0" in out.getvalue()


# --- comp ------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_comp_moves_a_guest_to_provisional(make_user, role):
    actor, guest = make_user(role), make_user("guest")
    onboarding.comp(actor, guest)
    guest.refresh_from_db()
    assert guest.status == User.Status.ACTIVE and roles.trust_role(guest).name == "provisional"
    assert guest.role_assignments.get(role__name="guest").revoked_at is not None
    subscription = Subscription.objects.get(user=guest)
    assert (subscription.status, subscription.comped_by) == (Subscription.Status.COMPED, actor)
    assert AuditEntry.objects.filter(action="subscription.comp", actor=actor).exists()


def test_comp_is_admin_only_and_for_guests_only(make_user):
    with pytest.raises(PermissionDenied):
        onboarding.comp(make_user("moderator"), make_user("guest"))
    with pytest.raises(PermissionDenied):
        onboarding.comp(make_user("admin"), make_user("full"))


# --- access and pages ------------------------------------------------------------------------


@pytest.fixture
def invited_client(client, make_user):
    invitation = accepted(make_user("full"), submit=False)
    client.force_login(invitation.invitee)
    return client, invitation


@pytest.mark.parametrize("path", ["/", "/invitations/", "/staff/onboarding/", "/staff/admin/"])
def test_invited_account_reaches_only_onboarding(invited_client, path):
    client, _ = invited_client
    assert client.get(path)["Location"] == "/onboarding/"
    assert client.post(path).status_code == 403


def test_invited_account_reads_nothing(invited_client, lobby):
    _, invitation = invited_client
    assert not can(invitation.invitee, "subforum.read", lobby)
    assert not can(invitation.invitee, "search.use")
    assert not can(invitation.invitee, "member.sponsor")


def test_onboarding_page(invited_client):
    client, invitation = invited_client
    assert client.get("/onboarding/").status_code == 200
    response = client.post("/onboarding/", {"real_name": "Real Name"})
    assert response["Location"] == "/onboarding/"
    assert IdentityRecord.objects.get(user=invitation.invitee).submitted_at is not None


def test_ended_account_cannot_sign_in(client, make_user):
    invitation = accepted(make_user("full"))
    onboarding.rescind(invitation.sponsor, invitation)
    response = client.post("/accounts/login/", {"login": invitation.invitee_email, "password": PASSWORD})
    assert response.status_code != 302 or "/onboarding/" not in response.get("Location", "")
    assert client.get("/onboarding/").status_code == 302  # back to login: not signed in


def test_review_queue_page_is_for_admins(client, make_user):
    full = make_user("full")
    enrol_totp(full)
    client.force_login(full)
    assert client.get("/staff/onboarding/").status_code == 403
    admin = make_user("admin")
    enrol_totp(admin)
    client.force_login(admin)
    accepted(make_user("full"))
    assert b"Real Name" in client.get("/staff/onboarding/").content


def test_end_to_end_through_the_pages(client, make_user, mailoutbox, django_capture_on_commit_callbacks):
    sponsor_user, admin = make_user("full"), make_user("admin")
    for user in (sponsor_user, admin):
        enrol_totp(user)

    client.force_login(sponsor_user)
    page = client.get("/invitations/")
    assert b"will hold one of your 1 sponsorship place" in page.content
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post("/invitations/", {"invitee_email": "newcomer@example.test", "vouching_notes": "School friend"})
    assert response["Location"] == "/invitations/?sent=slot"
    link = re.search(r"https?://\S+/invitations/accept/\S+/", mailoutbox[0].body).group(0)
    path = link.split("testserver", 1)[-1]
    client.logout()

    assert client.get(path).status_code == 200
    response = client.post(path, {"display_name": "Newcomer", "password1": PASSWORD, "password2": PASSWORD})
    assert response.status_code == 302
    newcomer = User.objects.get(email="newcomer@example.test")
    assert client.get("/onboarding/")["Location"] == "/accounts/2fa/totp/activate/"
    enrol_totp(newcomer)
    client.post("/onboarding/", {"real_name": "Newcomer Realname"})
    client.logout()

    client.force_login(admin)
    invitation = Invitation.objects.get(invitee=newcomer)
    client.post(f"/staff/onboarding/{invitation.pk}/approve/")
    newcomer.refresh_from_db()
    assert newcomer.status == User.Status.GUEST
    client.post(f"/staff/guests/{newcomer.pk}/comp/")
    assert roles.trust_role(newcomer).name == "provisional"
