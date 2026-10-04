"""A banned Guest or Provisional opens a review of their sponsor for an Admin or Owner to decide.
Nothing happens to the sponsor automatically."""

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError

from accounts.models import User
from core.dates import add_months
from core.permissions import can
from core.services import set_site_setting
from moderation import services
from moderation.models import ModerationAction, SponsorReview
from tests.factories import sponsor


def _ban(actor, target):
    return services.initiate_action(actor, target, "ban", internal_reason="abuse", public_summary="Abuse")


@pytest.mark.parametrize("member_role", ["guest", "provisional"])
def test_ban_opens_review_and_leaves_sponsor_alone(make_user, member_role):
    sponsor_user, member = make_user("full"), make_user(member_role)
    sponsor(sponsor_user, member)
    ban = _ban(make_user("admin"), member)
    review = SponsorReview.objects.get()
    assert (review.sponsor, review.banned_member, review.triggering_action) == (sponsor_user, member, ban)
    assert review.status == SponsorReview.Status.PENDING
    sponsor_user.refresh_from_db()
    assert sponsor_user.status == User.Status.ACTIVE
    assert not ModerationAction.objects.filter(target_user=sponsor_user).exists()


def test_review_opens_when_moderator_ban_is_approved(make_user):
    sponsor_user, member = make_user("tenured"), make_user("provisional")
    sponsor(sponsor_user, member)
    ban = services.initiate_action(make_user("moderator"), member, "ban", internal_reason="abuse")
    assert not SponsorReview.objects.exists()
    services.approve_action(make_user("admin"), ban)
    assert SponsorReview.objects.filter(sponsor=sponsor_user).exists()


def test_full_member_ban_opens_no_review(make_user):
    sponsor_user, member = make_user("tenured"), make_user("full")
    sponsor(sponsor_user, member)
    _ban(make_user("admin"), member)
    assert not SponsorReview.objects.exists()


@pytest.mark.parametrize("sponsor_role", ["admin", "owner"])
def test_admin_and_owner_sponsors_are_exempt(make_user, sponsor_role):
    member = make_user("provisional")
    sponsor(make_user(sponsor_role), member)
    _ban(make_user("owner"), member)
    assert not SponsorReview.objects.exists()


def test_setting_turns_reviews_off(make_user, owner):
    set_site_setting(owner, "sponsorship.review_on_member_ban", False)
    member = make_user("provisional")
    sponsor(make_user("full"), member)
    _ban(owner, member)
    assert not SponsorReview.objects.exists()


def _open_review(make_user, sponsor_role="full"):
    sponsor_user, member = make_user(sponsor_role), make_user("provisional")
    sponsor(sponsor_user, member)
    _ban(make_user("admin"), member)
    return SponsorReview.objects.get(), sponsor_user


def test_only_admin_or_owner_decides(make_user):
    review, _ = _open_review(make_user)
    assert not can(make_user("moderator"), "sponsor_review.decide", review)
    assert can(make_user("admin"), "sponsor_review.decide", review)
    assert can(make_user("owner"), "sponsor_review.decide", review)
    with pytest.raises(PermissionDenied):
        services.decide_sponsor_review(make_user("moderator"), review, "no_action")


def test_decide_no_action(make_user):
    review, sponsor_user = _open_review(make_user)
    services.decide_sponsor_review(make_user("admin"), review, "no_action", notes="Could not have known")
    review.refresh_from_db()
    assert review.status == SponsorReview.Status.DECIDED
    assert review.resulting_action is None
    assert not ModerationAction.objects.filter(target_user=sponsor_user).exists()


def test_decide_sponsoring_suspension_for_months(make_user):
    review, sponsor_user = _open_review(make_user, sponsor_role="tenured")
    admin = make_user("admin")
    services.decide_sponsor_review(
        admin, review, "sponsoring_suspension", months=6, invitees_transfer=True, notes="Vouched carelessly"
    )
    review.refresh_from_db()
    action = review.resulting_action
    assert action.kind == "sponsoring_suspension"
    assert action.status == ModerationAction.Status.ACTIVE
    assert action.approved_by is None  # Admin acted alone
    assert action.related_action == review.triggering_action
    assert action.ends_at.date() == add_months(action.starts_at, 6).date()
    assert review.suspension_months == 6 and review.invitees_transfer is True
    assert not can(sponsor_user, "member.sponsor")
    sponsor_user.refresh_from_db()
    assert sponsor_user.status == User.Status.ACTIVE


def test_suspension_needs_months(make_user):
    review, _ = _open_review(make_user)
    with pytest.raises(ValidationError):
        services.decide_sponsor_review(make_user("admin"), review, "sponsoring_suspension")
    with pytest.raises(ValidationError):
        services.decide_sponsor_review(make_user("admin"), review, "warning", months=3)


def test_decide_ban(make_user):
    review, sponsor_user = _open_review(make_user)
    services.decide_sponsor_review(make_user("owner"), review, "ban", public_summary="Sponsor of banned member")
    assert ModerationAction.objects.in_force().filter(target_user=sponsor_user, kind="ban").exists()
    assert not can(sponsor_user, "search.use")


def test_review_decided_once(make_user):
    review, _ = _open_review(make_user)
    services.decide_sponsor_review(make_user("admin"), review, "warning")
    with pytest.raises(PermissionDenied):
        services.decide_sponsor_review(make_user("admin"), review, "ban")


# --- opened by hand (rule 54) ----------------------------------------------------------------


@pytest.fixture
def known_sponsor(make_user):
    sponsor_user = make_user("full")
    sponsor(sponsor_user, make_user("provisional"))
    return sponsor_user


def test_admin_opens_a_review_by_hand_and_it_runs_like_an_automatic_one(known_sponsor, make_user):
    from moderation import queue

    admin = make_user("admin")
    review = services.open_sponsor_review(admin, known_sponsor, "brought back a banned person knowingly")
    assert (review.opened_by, review.banned_member, review.triggering_action) == (admin, None, None)
    assert [i.obj for i in queue.items(make_user("owner")) if i.type == "sponsor_review"] == [review]
    services.decide_sponsor_review(admin, review, "warning", notes="knew", public_summary="Sponsor warning")
    assert ModerationAction.objects.get(target_user=known_sponsor).kind == "warning"


def test_hand_opened_review_can_order_transfer(known_sponsor, make_user):
    from sponsorship.transfers import awaiting_sponsor

    admin = make_user("admin")
    review = services.open_sponsor_review(admin, known_sponsor, "invitees keep drawing warnings")
    services.decide_sponsor_review(admin, review, "sponsoring_suspension", months=2, invitees_transfer=True)
    sponsee = known_sponsor.sponsorships_given.get().member
    assert awaiting_sponsor(sponsee)


def test_hand_opened_review_needs_a_reason(known_sponsor, make_user):
    with pytest.raises(ValidationError):
        services.open_sponsor_review(make_user("admin"), known_sponsor, "  ")


@pytest.mark.parametrize("case", ["moderator", "admin_target", "never_sponsored", "self"])
def test_hand_opened_review_refused(known_sponsor, make_user, case):
    admin = make_user("admin")
    actor, target = admin, known_sponsor
    if case == "moderator":
        actor = make_user("moderator")
    elif case == "admin_target":
        target = make_user("admin")
        sponsor(target, make_user("provisional"))
    elif case == "never_sponsored":
        target = make_user("full")
    else:
        sponsor(admin, make_user("provisional"))
        target = admin
    with pytest.raises(PermissionDenied):
        services.open_sponsor_review(actor, target, "reason")
    assert not SponsorReview.objects.exists()


def test_open_review_from_the_staff_view(client, known_sponsor, make_user):
    from tests.factories import enrol_totp

    admin = make_user("admin")
    enrol_totp(admin)
    client.force_login(admin)
    page = client.get(f"/staff/members/{known_sponsor.slug}/").content
    assert b"Open a sponsor review" in page
    client.post(f"/staff/members/{known_sponsor.slug}/sponsor-review/", {"reason": "pattern of warnings"})
    assert SponsorReview.objects.get().open_reason == "pattern of warnings"
