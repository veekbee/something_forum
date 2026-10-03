"""Promotion workflow: Provisional to Full by recommendation, Moderator review and Admin decision;
Full to Tenured by an Admin or Moderator. Eligibility is shown; nobody is promoted on their own."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import roles
from accounts.models import User
from audit.models import AuditEntry
from core.permissions import can
from moderation.models import ModerationAction
from sponsorship import promotions
from sponsorship.models import Promotion
from tests.factories import make_post, make_thread, sponsor


@pytest.fixture
def eligible(make_user, general):
    """A Provisional for 91 days with 25 forum posts."""

    def make(days=91, posts=25):
        member = make_user("provisional", granted_at=timezone.now() - timedelta(days=days))
        thread = make_thread(general, member)
        for i in range(posts):
            make_post(thread, member, ago=timedelta(days=i % days))
        return member

    return make


def _reviewed(make_user, member):
    promotion = promotions.recommend(make_user("full"), member)
    return promotions.review(make_user("moderator"), promotion, notes="Clean record")


# --- eligibility ---------------------------------------------------------------------------


def test_eligibility_needs_both_days_and_posts(eligible):
    assert promotions.full_promotion_eligible(eligible())
    assert not promotions.full_promotion_eligible(eligible(days=89))
    assert not promotions.full_promotion_eligible(eligible(posts=24))


def test_rejected_and_dm_posts_do_not_count(eligible, make_user):
    from tests.factories import make_dm

    member = eligible(posts=24)
    make_post(make_thread(member.posts.first().thread.subforum, member), member, rejected_at=timezone.now())
    make_post(make_dm(member, make_user("full")), member)
    assert not promotions.full_promotion_eligible(member)


def test_eligibility_list_excludes_open_promotions(eligible, make_user):
    first, second = eligible(), eligible()
    eligible(days=10)
    assert set(promotions.eligible_for_full()) == {first, second}
    promotions.recommend(make_user("full"), first)
    assert promotions.eligible_for_full() == [second]


def test_nobody_is_promoted_automatically(eligible):
    member = eligible()
    assert roles.trust_role(member).name == "provisional"


# --- Provisional to Full -------------------------------------------------------------------


def test_full_workflow(eligible, make_user):
    member = eligible()
    recommender, moderator, admin = make_user("full"), make_user("moderator"), make_user("admin")

    promotion = promotions.recommend(recommender, member)
    assert promotion.status == Promotion.Status.RECOMMENDED
    promotion = promotions.review(moderator, promotion, notes="One warning in week two; fine since")
    assert promotion.status == Promotion.Status.REVIEWED
    promotion = promotions.decide(admin, promotion, approve=True)

    promotion.refresh_from_db()
    assert promotion.status == Promotion.Status.APPROVED
    assert (promotion.recommended_by, promotion.reviewed_by, promotion.decided_by) == (recommender, moderator, admin)
    assert promotion.review_notes == "One warning in week two; fine since"
    assert roles.trust_role(member).name == "full"
    history = list(member.role_assignments.order_by("pk").values_list("role__name", "revoked_at"))
    assert history[0][0] == "provisional" and history[0][1] is not None
    assert history[1] == ("full", None)
    assert list(
        AuditEntry.objects.filter(target_type="sponsorship.promotion").order_by("pk").values_list("action", flat=True)
    ) == ["promotion.recommend", "promotion.review", "promotion.decide"]


def test_sponsor_may_recommend(eligible, make_user):
    member = eligible()
    sponsor_user = make_user("full")
    sponsor(sponsor_user, member)
    assert can(sponsor_user, "promotion.recommend", member)


def test_decline_keeps_role_and_allows_a_later_recommendation(eligible, make_user):
    member = eligible()
    promotion = promotions.decide(make_user("admin"), _reviewed(make_user, member), approve=False, notes="Not yet")
    assert promotion.status == Promotion.Status.DECLINED
    assert roles.trust_role(member).name == "provisional"
    assert can(make_user("full"), "promotion.recommend", member)


@pytest.mark.parametrize("role", ["provisional", "guest"])
def test_only_full_and_above_recommend(eligible, make_user, role):
    with pytest.raises(PermissionDenied):
        promotions.recommend(make_user(role), eligible())


def test_ineligible_member_cannot_be_recommended(eligible, make_user):
    with pytest.raises(PermissionDenied):
        promotions.recommend(make_user("full"), eligible(days=30))


def test_one_open_promotion_per_member(eligible, make_user):
    member = eligible()
    promotions.recommend(make_user("full"), member)
    with pytest.raises(PermissionDenied):
        promotions.recommend(make_user("tenured"), member)
    with pytest.raises(IntegrityError), transaction.atomic():
        Promotion.objects.create(
            member=member, from_role_id=member.promotions.get().from_role_id,
            to_role_id=member.promotions.get().to_role_id,
        )


def test_review_needs_a_moderator(eligible, make_user):
    promotion = promotions.recommend(make_user("full"), eligible())
    with pytest.raises(PermissionDenied):
        promotions.review(make_user("tenured"), promotion)
    promotions.review(make_user("moderator"), promotion)


def test_scoped_moderator_may_review(eligible, make_user, general):
    from tests.factories import grant

    scoped = make_user("tenured")
    grant(scoped, "moderator", scope_subforum=general)
    promotions.review(scoped, promotions.recommend(make_user("full"), eligible()))


def test_decision_needs_admin_or_owner_and_a_review(eligible, make_user):
    member = eligible()
    promotion = promotions.recommend(make_user("full"), member)
    with pytest.raises(PermissionDenied):
        promotions.decide(make_user("admin"), promotion, approve=True)
    promotion = promotions.review(make_user("moderator"), promotion)
    with pytest.raises(PermissionDenied):
        promotions.decide(make_user("moderator"), promotion, approve=True)
    promotions.decide(make_user("owner"), promotion, approve=True)


def test_decided_promotion_cannot_be_decided_again(eligible, make_user):
    promotion = promotions.decide(make_user("admin"), _reviewed(make_user, eligible()), approve=True)
    with pytest.raises(PermissionDenied):
        promotions.decide(make_user("admin"), promotion, approve=False)


def test_banned_member_cannot_be_promoted(eligible, make_user):
    member = eligible()
    promotion = _reviewed(make_user, member)
    User.objects.filter(pk=member.pk).update(status=User.Status.BANNED)
    with pytest.raises(PermissionDenied):
        promotions.decide(make_user("admin"), promotion, approve=True)


def test_probation_record_for_the_reviewer(eligible, make_user):
    member = eligible()
    admin = make_user("admin")
    warning = ModerationAction.objects.create(
        target_user=member, kind="warning", initiated_by=admin, status="active",
        starts_at=timezone.now(), internal_reason="tone",
    )
    ModerationAction.objects.create(target_user=member, kind="warning", initiated_by=make_user("moderator"),
                                    internal_reason="pending, not yet a record")
    thread = member.posts.first().thread
    make_post(thread, member, is_held=True)
    make_post(thread, member, rejected_at=timezone.now())
    make_post(thread, member, released_at=timezone.now())

    record = promotions.probation_record(member)
    assert record["actions"] == [warning]
    assert record["posts"] == 27
    assert record["held_posts"] == 3
    assert record["rejected_posts"] == 1


# --- Full to Tenured -----------------------------------------------------------------------


@pytest.fixture
def long_full(make_user):
    def make(days=91):
        return make_user("full", granted_at=timezone.now() - timedelta(days=days))

    return make


@pytest.mark.parametrize("role", ["moderator", "admin", "owner"])
def test_admin_or_moderator_promotes_to_tenured(long_full, make_user, role):
    member = long_full()
    actor = make_user(role)
    promotion = promotions.promote_to_tenured(actor, member, notes="Good standing")
    assert promotion.status == Promotion.Status.APPROVED and promotion.decided_by == actor
    assert promotion.recommended_by is None
    assert roles.trust_role(member).name == "tenured"


def test_tenured_needs_ninety_days_as_full(long_full, make_user):
    with pytest.raises(PermissionDenied):
        promotions.promote_to_tenured(make_user("admin"), long_full(days=60))


def test_tenured_promotion_by_non_staff_is_denied(long_full, make_user):
    with pytest.raises(PermissionDenied):
        promotions.promote_to_tenured(make_user("tenured"), long_full())


def test_tenured_ends_active_sponsorship_and_frees_the_slot(long_full, make_user):
    from sponsorship import capacity

    sponsor_user, member = make_user("full"), long_full()
    row = sponsor(sponsor_user, member)
    assert capacity.at_capacity(sponsor_user)
    promotions.promote_to_tenured(make_user("moderator"), member)
    row.refresh_from_db()
    assert row.ended_at is not None and row.end_reason == "tenured"
    assert row.is_original  # the pedigree keeps the link as history
    assert not capacity.at_capacity(sponsor_user)
