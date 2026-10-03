"""Sponsorship caps follow the highest unrevoked global role; Admins and Owners have none.
Slot order and the waitlist are tested in test_onboarding.py."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts.models import User
from core.permissions import can
from core.services import set_site_setting
from moderation.models import ModerationAction
from sponsorship import capacity
from tests.factories import grant, invite, sponsor


def _fill(sponsor_user, n, make_user):
    for _ in range(n):
        sponsor(sponsor_user, make_user("provisional"))


def _invite(sponsor_user):
    return invite(sponsor_user)[0]


@pytest.mark.parametrize("role, cap", [("full", 1), ("tenured", 3), ("moderator", 7)])
def test_cap_by_role(make_user, role, cap):
    member = make_user(role)
    _fill(member, cap - 1, make_user)
    assert not capacity.at_capacity(member)
    _fill(member, 1, make_user)
    assert capacity.at_capacity(member)


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admin_and_owner_have_no_cap(make_user, role):
    member = make_user(role)
    _fill(member, 12, make_user)
    assert capacity.cap_for(member) is None
    assert not capacity.at_capacity(member)


def test_scoped_moderator_keeps_tenured_cap(make_user, general):
    member = make_user("tenured")
    grant(member, "moderator", scope_subforum=general)
    assert capacity.cap_for(member) == 3


def test_ended_sponsorship_frees_a_slot(make_user):
    member = make_user("full")
    row = sponsor(member, make_user("provisional"))
    assert capacity.at_capacity(member)
    row.ended_at, row.end_reason = timezone.now(), "tenured"
    row.save()
    assert not capacity.at_capacity(member)


@pytest.mark.parametrize("role", ["guest", "provisional"])
def test_below_full_cannot_sponsor(make_user, role):
    assert not can(make_user(role), "member.sponsor")


def test_unpaid_or_restricted_account_cannot_sponsor(make_user):
    assert not can(make_user("full", status=User.Status.READ_ONLY), "member.sponsor")


def test_cap_is_a_setting(make_user, owner):
    set_site_setting(owner, "sponsorship.cap.full", 2)
    member = make_user("full")
    _fill(member, 1, make_user)
    assert not capacity.at_capacity(member)


def test_sponsoring_suspension_blocks_until_it_ends(make_user):
    member = make_user("tenured")
    action = ModerationAction.objects.create(
        target_user=member, kind="sponsoring_suspension", initiated_by=make_user("admin"),
        status="active", starts_at=timezone.now(), ends_at=timezone.now() + timedelta(days=60),
        internal_reason="review",
    )
    assert not can(member, "member.sponsor")
    with pytest.raises(PermissionDenied):
        _invite(member)
    action.ends_at = timezone.now() - timedelta(seconds=1)
    action.save()
    assert can(member, "member.sponsor")
