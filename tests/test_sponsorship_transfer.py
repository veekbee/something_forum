"""Sponsorship transfer (docs/DESIGN.md, Sponsorship transfer; rules 51 to 53)."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import services as account_services
from accounts.models import RoleAssignment, User
from billing import lapse, membership
from billing.models import LapsePeriod
from boards import dm
from core.permissions import can
from core.services import set_site_setting
from moderation import permanent, queue, services
from moderation.models import ModerationAction
from sponsorship import eligibility, transfers
from sponsorship.models import Sponsorship, SponsorshipOffer, SponsorshipTransfer
from tests.factories import enrol_totp, grant, make_post, make_thread, sponsor

Cause, Status = SponsorshipTransfer.Cause, SponsorshipTransfer.Status
DAY = timedelta(days=1)


def _ban(target, actor):
    return services.initiate_action(actor, target, "ban", internal_reason="abuse", public_summary="Abuse")


@pytest.fixture
def pair(make_user):
    """A Full sponsor with a Provisional sponsee."""
    sponsor_user, member = make_user("full"), make_user("provisional")
    sponsor(sponsor_user, member)
    return sponsor_user, member


def _restrict(user):
    """Put `user` past the restriction stage of a lapse."""
    User.objects.filter(pk=user.pk).update(status=User.Status.READ_ONLY)
    LapsePeriod.objects.create(user=user, started_at=timezone.now() - 100 * DAY)
    lapse.run_daily()
    user.refresh_from_db()


def _offer(offerer, member, notes="Known them ten years"):
    return transfers.make_offer(offerer, member, notes)


# --- causes ----------------------------------------------------------------------------------


def test_ban_of_sponsor_opens_transfer(pair, make_user):
    sponsor_user, member = pair
    _ban(sponsor_user, make_user("admin"))
    transfer = SponsorshipTransfer.objects.get(member=member)
    assert transfer.cause == Cause.SPONSOR_BANNED and transfer.status == Status.OPEN
    assert transfer.deadline_at - transfer.started_at == 30 * DAY
    # The old row stays active while the transfer is open.
    assert Sponsorship.objects.get(member=member).ended_at is None


def test_permanent_ban_opens_transfer(pair, owner):
    sponsor_user, member = pair
    permanent.impose(owner, sponsor_user, "same person as an earlier ban")
    assert SponsorshipTransfer.objects.get(member=member).cause == Cause.SPONSOR_BANNED


def test_pending_ban_opens_nothing_until_approved(pair, make_user):
    sponsor_user, member = pair
    ban = services.initiate_action(make_user("moderator"), sponsor_user, "ban", internal_reason="abuse")
    assert not SponsorshipTransfer.objects.exists()
    services.approve_action(make_user("admin"), ban)
    assert SponsorshipTransfer.objects.filter(member=member).exists()


def test_losing_the_role_that_allows_sponsoring(pair, owner):
    sponsor_user, member = pair
    full = RoleAssignment.objects.get(user=sponsor_user, role__name="full")
    with transaction.atomic():
        account_services.grant_role(owner, sponsor_user, "provisional")
    account_services.revoke_role(owner, full)
    assert SponsorshipTransfer.objects.get(member=member).cause == Cause.SPONSOR_ROLE_LOST


def test_a_shrinking_cap_moves_nobody(make_user, owner):
    mod = make_user("tenured")
    assignment = grant(mod, "moderator")
    sponsees = [make_user("provisional") for _ in range(5)]
    for s in sponsees:
        sponsor(mod, s)
    account_services.revoke_role(owner, assignment)
    assert not SponsorshipTransfer.objects.exists()


def test_lapse_restriction_stage_opens_transfer(pair):
    sponsor_user, member = pair
    _restrict(sponsor_user)
    assert SponsorshipTransfer.objects.get(member=member).cause == Cause.SPONSOR_LAPSE_RESTRICTED


def test_lapse_before_the_restriction_stage_opens_nothing(pair):
    sponsor_user, _ = pair
    User.objects.filter(pk=sponsor_user.pk).update(status=User.Status.READ_ONLY)
    LapsePeriod.objects.create(user=sponsor_user, started_at=timezone.now() - 10 * DAY)
    lapse.run_daily()
    assert not SponsorshipTransfer.objects.exists()


def test_sponsor_review_can_order_transfer(make_user):
    sponsor_user, banned, other = make_user("full"), make_user("provisional"), make_user("guest")
    sponsor(sponsor_user, banned)
    sponsor(sponsor_user, other)
    admin = make_user("admin")
    _ban(banned, admin)
    review = sponsor_user.sponsor_reviews.get()
    services.decide_sponsor_review(admin, review, "sponsoring_suspension", months=3, invitees_transfer=True)
    transfer = SponsorshipTransfer.objects.get(member=other)
    assert transfer.cause == Cause.SPONSOR_REVIEW
    # A banned member already counted; they get a transfer too, but nothing else changes for them.
    assert SponsorshipTransfer.objects.filter(member=banned).exists()


def test_review_suspension_without_transfer_moves_nobody(make_user):
    sponsor_user, banned, other = make_user("full"), make_user("provisional"), make_user("guest")
    sponsor(sponsor_user, banned)
    sponsor(sponsor_user, other)
    admin = make_user("admin")
    _ban(banned, admin)
    services.decide_sponsor_review(admin, sponsor_user.sponsor_reviews.get(), "sponsoring_suspension", months=3)
    assert not SponsorshipTransfer.objects.exists()


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admin_and_owner_sponsors_never_trigger_one(make_user, role):
    sponsor_user, member = make_user(role), make_user("provisional")
    sponsor(sponsor_user, member)
    assert transfers.open_for_sponsor(sponsor_user, Cause.SPONSOR_BANNED) == []
    assert not SponsorshipTransfer.objects.exists()


def test_one_open_transfer_per_member(pair, make_user):
    sponsor_user, member = pair
    _ban(sponsor_user, make_user("admin"))
    transfers.open_for_sponsor(sponsor_user, Cause.SPONSOR_REVIEW)
    assert SponsorshipTransfer.objects.filter(member=member).count() == 1


# --- while waiting ---------------------------------------------------------------------------


@pytest.fixture
def waiting(pair, make_user):
    sponsor_user, member = pair
    _ban(sponsor_user, make_user("admin"))
    return sponsor_user, member


def test_waiting_sponsee_is_read_only(waiting, general):
    _, member = waiting
    assert can(member, "subforum.read", general)
    refused = can(member, "subforum.start_thread", general)
    assert not refused and refused.code == "transfer"
    post = make_post(make_thread(general, member), member)
    assert not can(member, "post.edit", post)


def test_waiting_full_sponsee_cannot_sponsor(make_user):
    sponsor_user, member = make_user("tenured"), make_user("full")
    sponsor(sponsor_user, member)
    assert can(member, "member.sponsor")
    _ban(sponsor_user, make_user("admin"))
    assert not can(member, "member.sponsor")


def test_waiting_is_not_shown_on_the_public_profile(waiting, client, make_user):
    _, member = waiting
    viewer = make_user("provisional")
    enrol_totp(viewer)
    client.force_login(viewer)
    page = client.get(f"/members/{member.slug}/").content
    assert b"sponsor" not in page.lower() and b"vouch" not in page.lower()


def test_waiting_sponsee_sees_the_notice(waiting, client):
    _, member = waiting
    enrol_totp(member)
    client.force_login(member)
    assert b"You need a new sponsor" in client.get("/").content
    assert b"No offers yet" in client.get("/sponsorship/transfer/").content


def test_provisional_clock_pauses_while_waiting(make_user, owner, general):
    set_site_setting(owner, "promotion.full.min_posts", 1)
    member = make_user("provisional", granted_at=timezone.now() - 100 * DAY)
    sponsorship = sponsor(make_user("full"), member)
    make_post(make_thread(general, member), member)
    assert eligibility.full_promotion_eligible(member)
    SponsorshipTransfer.objects.create(
        member=member, sponsorship=sponsorship, cause=Cause.SPONSOR_BANNED, started_at=timezone.now() - 20 * DAY,
        deadline_at=timezone.now() + 10 * DAY,
    )
    assert not eligibility.full_promotion_eligible(member)


def test_overlapping_lapse_and_wait_count_once(make_user):
    member = make_user("provisional")
    since = timezone.now() - 100 * DAY
    sponsorship = sponsor(make_user("full"), member)
    LapsePeriod.objects.create(user=member, started_at=since + 10 * DAY, ended_at=since + 30 * DAY)
    SponsorshipTransfer.objects.create(
        member=member, sponsorship=sponsorship, cause=Cause.SPONSOR_BANNED, status=Status.RESUMED,
        started_at=since + 20 * DAY, ended_at=since + 40 * DAY, deadline_at=since + 50 * DAY,
    )
    assert eligibility.paused_since(member, since) == 30 * DAY


def test_waiting_sponsee_dms(make_user):
    old, member, stranger, offerer = make_user("tenured"), make_user("full"), make_user("full"), make_user("full")
    sponsor(old, member)
    admin = make_user("admin")
    assert dm.may_message(member, stranger)
    _ban(old, admin)
    assert not dm.may_message(member, stranger)
    assert dm.may_message(member, admin)
    # The banned sponsor messages only Admins and Owners.
    assert not dm.may_message(member, old)
    offer = _offer(offerer, member)
    assert dm.may_message(member, offerer) and dm.may_message(offerer, member)
    transfers.withdraw_offer(offerer, offer)
    assert not dm.may_message(member, offerer)


def test_old_sponsor_still_reachable_when_the_cause_allows(make_user):
    old, banned, member = make_user("full"), make_user("provisional"), make_user("full")
    sponsor(old, banned)
    sponsor(old, member)
    admin = make_user("admin")
    _ban(banned, admin)
    services.decide_sponsor_review(admin, old.sponsor_reviews.get(), "sponsoring_suspension", months=1,
                                   invitees_transfer=True)
    assert transfers.awaiting_sponsor(member)
    assert dm.may_message(member, old)


# --- offers ----------------------------------------------------------------------------------


def test_offer_and_acceptance_complete_the_transfer(waiting, make_user, general):
    old, member = waiting
    first, second = make_user("full"), make_user("tenured")
    accepted, other = _offer(first, member), _offer(second, member)
    transfer = transfers.accept_offer(member, accepted)
    assert transfer.status == Status.COMPLETED and transfer.ended_at is not None
    other.refresh_from_db()
    assert other.status == SponsorshipOffer.Status.LAPSED
    previous = Sponsorship.objects.get(member=member, sponsor=old)
    assert previous.end_reason == Sponsorship.EndReason.TRANSFERRED and previous.ended_at is not None
    current = Sponsorship.objects.get(member=member, ended_at__isnull=True)
    assert current.sponsor == first and current.previous == previous and not current.is_original
    assert transfer.new_sponsorship == current
    assert not transfers.awaiting_sponsor(member)
    assert can(member, "subforum.start_thread", general)


def test_offers_need_vouching_notes(waiting, make_user):
    _, member = waiting
    with pytest.raises(ValidationError):
        _offer(make_user("full"), member, notes="  ")


@pytest.mark.parametrize("who", ["provisional", "guest", "lapsed", "suspended_sponsoring", "banned", "admin",
                                 "old_sponsor", "self"])
def test_offers_refused_from_ineligible_members(waiting, make_user, who):
    old, member = waiting
    admin = make_user("admin")
    if who in ("provisional", "guest"):
        offerer = make_user(who)
    elif who == "lapsed":
        offerer = make_user("full", status=User.Status.READ_ONLY)
    elif who == "suspended_sponsoring":
        offerer = make_user("full")
        services.initiate_action(admin, offerer, "sponsoring_suspension", internal_reason="review",
                                 ends_at=timezone.now() + 30 * DAY)
    elif who == "banned":
        offerer = make_user("full")
        _ban(offerer, admin)
    elif who == "admin":
        offerer = admin
    elif who == "old_sponsor":
        offerer = old
    else:
        offerer = member
    with pytest.raises(PermissionDenied):
        _offer(offerer, member)


def test_offers_refused_from_a_full_sponsor(waiting, make_user):
    _, member = waiting
    offerer = make_user("full")  # cap 1
    sponsor(offerer, make_user("provisional"))
    with pytest.raises(PermissionDenied):
        _offer(offerer, member)


def test_acceptance_refused_when_offerer_has_filled_up_meanwhile(waiting, make_user):
    _, member = waiting
    offerer = make_user("full")
    offer = _offer(offerer, member)
    sponsor(offerer, make_user("provisional"))
    with pytest.raises(PermissionDenied):
        transfers.accept_offer(member, offer)
    assert transfers.awaiting_sponsor(member)


def test_no_offer_to_someone_not_waiting(pair, make_user):
    _, member = pair
    with pytest.raises(PermissionDenied):
        _offer(make_user("full"), member)


def test_only_the_sponsee_answers_an_offer(waiting, make_user):
    _, member = waiting
    offer = _offer(make_user("full"), member)
    with pytest.raises(PermissionDenied):
        transfers.accept_offer(make_user("admin"), offer)
    transfers.decline_offer(member, offer)
    offer.refresh_from_db()
    assert offer.status == SponsorshipOffer.Status.DECLINED
    assert transfers.awaiting_sponsor(member)


def test_offer_flow_through_the_pages(waiting, client, make_user):
    _, member = waiting
    offerer = make_user("full")
    enrol_totp(offerer)
    client.force_login(offerer)
    assert f"/sponsorship/vouch/{member.slug}/".encode() in client.get(f"/members/{member.slug}/").content
    client.post(f"/sponsorship/vouch/{member.slug}/", {"vouching_notes": "Worked together for years"})
    offer = SponsorshipOffer.objects.get(offerer=offerer)
    enrol_totp(member)
    client.force_login(member)
    assert b"Worked together for years" in client.get("/sponsorship/transfer/").content
    client.post(f"/sponsorship/offers/{offer.pk}/accept/")
    assert Sponsorship.objects.get(member=member, ended_at__isnull=True).sponsor == offerer


# --- Admin and Owner decisions, the deadline --------------------------------------------------


def test_admin_steps_in_as_sponsor(waiting, make_user):
    _, member = waiting
    admin = make_user("admin")
    transfer = transfers.step_in(admin, transfers.open_transfer(member))
    assert transfer.status == Status.DECIDED and transfer.decision == "admin_sponsored"
    assert Sponsorship.objects.get(member=member, ended_at__isnull=True).sponsor == admin


def test_moderators_cannot_decide_transfers(waiting, make_user):
    _, member = waiting
    with pytest.raises(PermissionDenied):
        transfers.step_in(make_user("moderator"), transfers.open_transfer(member))


def test_deadline_queues_an_item_and_does_nothing_else(waiting, make_user):
    _, member = waiting
    admin, mod = make_user("admin"), make_user("moderator")
    transfer = transfers.open_transfer(member)
    assert not [i for i in queue.items(admin) if i.type == "transfer"]
    SponsorshipTransfer.objects.filter(pk=transfer.pk).update(deadline_at=timezone.now() - DAY)
    lapse.run_daily()
    services.expire_actions()
    assert [i.obj for i in queue.items(admin) if i.type == "transfer"] == [transfer]
    assert not [i for i in queue.items(mod) if i.type == "transfer"]
    member.refresh_from_db()
    assert member.status == User.Status.ACTIVE and transfers.awaiting_sponsor(member)


def test_extending_moves_the_deadline(waiting, make_user):
    _, member = waiting
    admin = make_user("admin")
    transfer = transfers.open_transfer(member)
    SponsorshipTransfer.objects.filter(pk=transfer.pk).update(deadline_at=timezone.now() - DAY)
    transfer = transfers.extend(admin, transfer, 14)
    assert transfer.status == Status.OPEN and transfer.deadline_at > timezone.now() + 13 * DAY
    assert not [i for i in queue.items(admin) if i.type == "transfer"]


def test_removal_only_after_the_deadline(waiting, make_user):
    _, member = waiting
    admin = make_user("admin")
    own_sponsee = make_user("guest")
    Sponsorship.objects.create(sponsor=member, member=own_sponsee, sponsor_role=member.role_assignments.get().role)
    transfer = transfers.open_transfer(member)
    with pytest.raises(PermissionDenied):
        transfers.remove_member(admin, transfer, "no sponsor found")
    SponsorshipTransfer.objects.filter(pk=transfer.pk).update(deadline_at=timezone.now() - DAY)
    with pytest.raises(ValidationError):
        transfers.remove_member(admin, transfer, " ")
    transfers.remove_member(admin, transfer, "no sponsor found")
    member.refresh_from_db()
    assert member.status == User.Status.REMOVED and not member.is_active
    assert Sponsorship.objects.get(member=member).end_reason == Sponsorship.EndReason.MEMBER_REMOVED
    # The removed member counts as a sponsor who has left.
    assert SponsorshipTransfer.objects.get(member=own_sponsee).cause == Cause.SPONSOR_LEFT


def test_removal_stops_stripe_renewal(waiting, make_user, monkeypatch):
    from billing import stripe_api
    from billing.models import Subscription

    _, member = waiting
    Subscription.objects.create(user=member, stripe_subscription_id="sub_test", status=Subscription.Status.ACTIVE)
    cancelled = []
    monkeypatch.setattr(stripe_api, "cancel_subscription", cancelled.append)
    transfer = transfers.open_transfer(member)
    SponsorshipTransfer.objects.filter(pk=transfer.pk).update(deadline_at=timezone.now() - DAY)
    transfers.remove_member(make_user("admin"), transfer, "no sponsor found")
    assert cancelled == ["sub_test"]


def test_tenure_closes_a_waiting_transfer(make_user):
    from sponsorship import promotions

    old, member = make_user("tenured"), make_user("full", granted_at=timezone.now() - 400 * DAY)
    sponsor(old, member)
    admin = make_user("admin")
    _ban(old, admin)
    promotions.promote_to_tenured(admin, member)
    assert not transfers.awaiting_sponsor(member)
    assert SponsorshipTransfer.objects.get(member=member).status == Status.COMPLETED


# --- resuming, and finality ------------------------------------------------------------------


def test_lifted_ban_resumes_the_original_sponsorship(waiting, make_user):
    old, member = waiting
    offer = _offer(make_user("full"), member)
    ban = ModerationAction.objects.get(target_user=old, kind="ban")
    services.lift_ban(make_user("admin"), ban, "mistake")
    transfer = SponsorshipTransfer.objects.get(member=member)
    assert transfer.status == Status.RESUMED and transfer.ended_at is not None
    offer.refresh_from_db()
    assert offer.status == SponsorshipOffer.Status.LAPSED
    assert Sponsorship.objects.get(member=member, ended_at__isnull=True).sponsor == old


def test_expired_ban_resumes(pair, make_user):
    old, member = pair
    services.initiate_action(make_user("admin"), old, "ban", internal_reason="abuse",
                             ends_at=timezone.now() + DAY)
    assert transfers.awaiting_sponsor(member)
    ModerationAction.objects.filter(target_user=old).update(ends_at=timezone.now() - timedelta(seconds=1))
    services.expire_actions()
    assert SponsorshipTransfer.objects.get(member=member).status == Status.RESUMED


def test_payment_by_a_lapsed_sponsor_resumes(pair):
    old, member = pair
    _restrict(old)
    assert transfers.awaiting_sponsor(member)
    with transaction.atomic():
        membership.restore(old, timezone.now() + 365 * DAY)
    assert SponsorshipTransfer.objects.get(member=member).status == Status.RESUMED


def test_regaining_the_role_resumes(pair, owner):
    old, member = pair
    full = RoleAssignment.objects.get(user=old, role__name="full")
    with transaction.atomic():
        account_services.grant_role(owner, old, "provisional")
    account_services.revoke_role(owner, full)
    assert transfers.awaiting_sponsor(member)
    with transaction.atomic():
        account_services.grant_role(owner, old, "full")
    assert SponsorshipTransfer.objects.get(member=member).status == Status.RESUMED


def test_no_resumption_while_another_cause_holds(pair, make_user):
    old, member = pair
    admin = make_user("admin")
    _restrict(old)
    services.initiate_action(admin, old, "ban", internal_reason="abuse")
    with transaction.atomic():
        membership.restore(old, timezone.now() + 365 * DAY)
    assert transfers.awaiting_sponsor(member)


def test_sponsor_review_transfer_is_final(make_user):
    old, banned, member = make_user("full"), make_user("provisional"), make_user("full")
    sponsor(old, banned)
    sponsor(old, member)
    admin = make_user("admin")
    _ban(banned, admin)
    services.decide_sponsor_review(admin, old.sponsor_reviews.get(), "sponsoring_suspension", months=1,
                                   invitees_transfer=True)
    with transaction.atomic():
        transfers.resume_for_sponsor(old)
    assert transfers.awaiting_sponsor(member)


def test_annulled_permanent_ban_does_not_resume(pair, owner):
    old, member = pair
    action = permanent.impose(owner, old, "thought to be an earlier banned person")
    permanent.annul(owner, action, "wrong person")
    with transaction.atomic():
        transfers.resume_for_sponsor(old)
    assert transfers.awaiting_sponsor(member)


def test_review_ordering_transfer_makes_an_open_one_final(make_user):
    old, banned, member = make_user("full"), make_user("provisional"), make_user("full")
    sponsor(old, banned)
    sponsor(old, member)
    admin = make_user("admin")
    _ban(banned, admin)
    ban = _ban(old, admin)  # opens a resumable transfer for member
    services.decide_sponsor_review(admin, old.sponsor_reviews.get(), "sponsoring_suspension", months=1,
                                   invitees_transfer=True)
    services.lift_ban(admin, ban, "paid")
    assert transfers.awaiting_sponsor(member)


def test_transfers_are_audited(waiting, make_user):
    from audit.models import AuditEntry

    _, member = waiting
    offerer = make_user("full")
    transfers.accept_offer(member, _offer(offerer, member))
    actions = set(AuditEntry.objects.values_list("action", flat=True))
    assert {"sponsorship.transfer_open", "sponsorship.offer", "sponsorship.transfer_complete"} <= actions


def test_lifted_ban_does_not_resume_while_still_lapse_restricted(pair, make_user):
    old, member = pair
    admin = make_user("admin")
    _restrict(old)
    ban = services.initiate_action(admin, old, "ban", internal_reason="abuse")
    services.lift_ban(admin, ban, "mistake")
    assert transfers.awaiting_sponsor(member)


def test_withdrawn_offer_leaves_the_conversation_readable_but_closed(waiting, make_user):
    from boards import messages

    _, member = waiting
    offerer = make_user("full")
    offer = _offer(offerer, member)
    conversation, _ = messages.start(member, [offerer], "Vouching", "Thank you for offering")
    transfers.withdraw_offer(offerer, offer)
    assert can(member, "thread.read", conversation) and can(offerer, "thread.read", conversation)
    assert not can(member, "thread.reply", conversation) and not can(offerer, "thread.reply", conversation)
