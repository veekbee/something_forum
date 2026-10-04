"""Build step 5 part 2: the lapse clock, its two stages, comps and the billing reminders (rules 42 to
44), on a fixed clock."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts import roles
from accounts.models import User
from accounts.services import grant_role, revoke_role
from billing import lapse, stripe_api, webhooks
from billing.models import LapsePeriod, Subscription
from boards import messages
from core.models import Notification
from core.permissions import can
from tests.factories import make_post, make_thread, sponsor
from tests.test_billing_webhooks import invoice_paid

NOW = timezone.now()
DAY = timedelta(days=1)


@pytest.fixture(autouse=True)
def no_stripe(monkeypatch):
    cancelled = []
    monkeypatch.setattr(stripe_api, "cancel_subscription", lambda sub_id: cancelled.append(sub_id))
    return cancelled


def _paid(member, until, sub_id="sub_1"):
    from billing.membership import set_clock

    sub, _ = Subscription.objects.get_or_create(user=member)
    sub.status, sub.current_period_end, sub.stripe_subscription_id = "active", until, sub_id
    set_clock(sub, until)
    sub.save()
    return sub


def _kinds(user):
    return list(Notification.objects.filter(recipient=user).values_list("kind", flat=True))


# --- the clock -----------------------------------------------------------------------------


def test_grace_runs_from_the_period_end_whatever_stripe_says(make_user, no_stripe):
    member = make_user("full")
    sub = _paid(member, NOW)
    Subscription.objects.filter(pk=sub.pk).update(status="past_due")  # Stripe still retrying
    lapse.run_daily(now=NOW + 13 * DAY)
    member.refresh_from_db()
    assert member.status == User.Status.ACTIVE
    lapse.run_daily(now=NOW + 14 * DAY + timedelta(minutes=1))
    member.refresh_from_db()
    assert member.status == User.Status.READ_ONLY
    assert no_stripe == ["sub_1"]  # the forum cancels the Stripe subscription
    sub.refresh_from_db()
    assert sub.stripe_subscription_id == "" and sub.status == "lapsed"
    assert LapsePeriod.objects.get(user=member).ended_at is None
    assert "billing.read_only" in _kinds(member)


def test_reminders_go_out_once(make_user):
    member = make_user("full")
    _paid(member, NOW + 20 * DAY)
    lapse.run_daily(now=NOW)
    lapse.run_daily(now=NOW + DAY)
    assert _kinds(member).count("billing.renewal_reminder") == 1
    lapse.run_daily(now=NOW + 32 * DAY)  # inside the 3 days before read-only
    lapse.run_daily(now=NOW + 33 * DAY)
    assert _kinds(member).count("billing.read_only_soon") == 1


def test_cancelling_keeps_access_to_the_end_of_the_year(make_user):
    member = make_user("full")
    sub = _paid(member, NOW + 100 * DAY)
    Subscription.objects.filter(pk=sub.pk).update(cancel_at_period_end=True)
    lapse.run_daily(now=NOW + 99 * DAY)
    member.refresh_from_db()
    assert member.status == User.Status.ACTIVE
    assert "billing.renewal_reminder" not in _kinds(member)


# --- the two stages ------------------------------------------------------------------------


@pytest.fixture
def lapsed(make_user, general):
    """A Full member who sponsors someone, lapsed 15 days after their year ended."""
    member = make_user("full")
    sponsee = make_user("provisional")
    sponsor(member, sponsee)
    make_post(make_thread(general, member), member)
    _paid(member, NOW)
    lapse.run_daily(now=NOW + 15 * DAY)
    member.refresh_from_db()
    return member, sponsee


def test_read_only_lapsed_reads_but_posts_nothing(lapsed, general):
    member, _ = lapsed
    assert can(member, "subforum.read", general)
    assert not can(member, "thread.reply", make_thread(general, member))
    assert not can(member, "member.sponsor")


def test_sub_forums_can_close_to_lapsed_readers(lapsed, serious):
    member, _ = lapsed
    serious.settings["subforum.readable_when_lapsed"] = False
    serious.save()
    assert not can(member, "subforum.read", serious)


def test_restriction_stage_narrows_access(lapsed, general, make_user):
    from boards import visibility

    member, sponsee = lapsed
    own_sponsor = make_user("tenured")
    sponsor(own_sponsor, member)
    with_sponsor, _ = messages.start(own_sponsor, [member], "", "how are you")
    from tests.factories import make_dm

    with_friend = make_dm(make_user("full"), own_sponsor, member)  # from before the lapse
    lapse.run_daily(now=NOW + (14 + 91) * DAY)
    assert lapse.is_restricted(member)
    assert not can(member, "subforum.read", general)
    assert not can(member, "search.use")
    assert visibility.post_history(member, member).exists()
    assert can(member, "thread.read", with_sponsor)
    assert not can(member, "thread.read", with_friend)
    assert can(member, "billing.view", member)
    # Interim: sponsees are left as they are until sponsorship transfer is designed.
    assert sponsee.sponsorships.get().ended_at is None


@pytest.mark.parametrize("stage_days", [5, 20, 120])  # in the grace, read-only, restricted
def test_payment_at_any_stage_restores_everything(make_user, general, stage_days):
    member = make_user("full")
    _paid(member, NOW)
    lapse.run_daily(now=NOW + stage_days * DAY)
    webhooks.process(invoice_paid(member, sub_id="sub_2", customer="cus_2",
                                  period_end=int((NOW + 400 * DAY).timestamp())))
    member.refresh_from_db()
    assert member.status == User.Status.ACTIVE and roles.trust_role(member).name == "full"
    assert can(member, "thread.reply", make_thread(general, member))
    assert not LapsePeriod.objects.filter(user=member, ended_at__isnull=True).exists()


def test_lapsed_members_cannot_recommend(lapsed, make_user):
    member, _ = lapsed
    assert not can(member, "promotion.recommend", make_user("provisional"))


def test_provisional_clock_pauses_while_lapsed(make_user, general):
    from sponsorship.eligibility import full_promotion_eligible

    member = make_user("provisional", granted_at=timezone.now() - 100 * DAY)
    thread = make_thread(general, member)
    for _ in range(25):
        make_post(thread, member)
    assert full_promotion_eligible(member)
    LapsePeriod.objects.create(user=member, started_at=timezone.now() - 30 * DAY, ended_at=timezone.now() - 10 * DAY)
    assert not full_promotion_eligible(member)  # 100 days less 20 lapsed is under 90


def test_lapsing_never_removes_an_account(lapsed):
    member, _ = lapsed
    lapse.run_daily(now=NOW + 1000 * DAY)
    member.refresh_from_db()
    assert member.status == User.Status.READ_ONLY


# --- comps ---------------------------------------------------------------------------------


def test_granting_and_revoking_staff_roles_starts_and_ends_the_comp(make_user, owner):
    member = make_user("tenured")
    admin = make_user("admin")
    assignment = grant_role(admin, member, "moderator")
    sub = Subscription.objects.get(user=member)
    assert (sub.status, sub.comp_reason) == ("comped", "staff")
    revoke_role(admin, assignment)
    sub.refresh_from_db()
    assert sub.status != "comped" and sub.read_only_at is not None
    assert sub.read_only_at <= timezone.now() + 14 * DAY + timedelta(minutes=1)


def test_leadership_can_keep_a_former_staff_member_comped(make_user):
    member, admin = make_user("tenured"), make_user("admin")
    assignment = grant_role(admin, member, "moderator")
    revoke_role(admin, assignment, keep_comped=True)
    sub = Subscription.objects.get(user=member)
    assert (sub.status, sub.comp_reason) == ("comped", "other")


def test_seed_comps_existing_staff(owner):
    assert Subscription.objects.get(user=owner).comp_reason == "staff"


def test_launch_turns_existing_comps_into_founding_comps_once(make_user, owner):
    from sponsorship import onboarding

    guest = make_user("guest")
    onboarding.comp(owner, guest)
    assert lapse.launch(now=NOW) == 1
    sub = Subscription.objects.get(user=guest)
    assert sub.comp_reason == "founding" and sub.comped_until.year == NOW.year + 1
    assert lapse.launch(now=NOW) == 0
    assert Subscription.objects.get(user=owner).comp_reason == "staff"


def test_founding_comps_warn_then_end(make_user, owner):
    from sponsorship import onboarding

    guest = make_user("guest")
    onboarding.comp(owner, guest)
    lapse.launch(now=NOW)
    until = Subscription.objects.get(user=guest).comped_until
    lapse.run_daily(now=until - 20 * DAY)
    assert "billing.founding_comp_ending" in _kinds(guest)
    lapse.run_daily(now=until + DAY)
    sub = Subscription.objects.get(user=guest)
    assert sub.status == "lapsed" and sub.read_only_at == until + 14 * DAY
    lapse.run_daily(now=until + 15 * DAY)
    guest.refresh_from_db()
    assert guest.status == User.Status.READ_ONLY


def test_only_an_owner_extends_a_comp(make_user, owner):
    from sponsorship import onboarding

    guest = make_user("guest")
    onboarding.comp(owner, guest)
    with pytest.raises(PermissionDenied):
        lapse.extend_comp(make_user("admin"), guest, NOW + 400 * DAY)
    lapse.extend_comp(owner, guest, NOW + 400 * DAY)
    assert Subscription.objects.get(user=guest).comped_until == NOW + 400 * DAY


def test_staff_never_lapse(make_user):
    admin = make_user("admin")
    _paid(admin, NOW)
    lapse.run_daily(now=NOW + 30 * DAY)
    admin.refresh_from_db()
    assert admin.status == User.Status.ACTIVE
