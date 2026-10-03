import io

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command

from accounts import roles
from accounts.models import Role, RoleAssignment, User
from audit.models import AuditEntry
from boards.models import SubForum
from core import registry
from core.models import SiteSetting
from core.services import set_site_setting


def test_registry_defaults_match_design():
    expected = {
        "sponsorship.cap.full": 1,
        "sponsorship.cap.tenured": 3,
        "sponsorship.cap.moderator": 7,
        "sponsorship.cap.admin": None,
        "sponsorship.cap.owner": None,
        "sponsorship.transfer_grace_days": 30,
        "sponsorship.review_on_member_ban": True,
        "promotion.full.min_days": 90,
        "promotion.full.min_posts": 25,
        "promotion.tenured.min_days": 90,
        "provisional.held_posts": 5,
        "billing.lapse_grace_days": 14,
        "billing.ban_reversal_fee_cents": 1000,
        "auth.require_totp": True,
        "retention.audit_years_after_departure": 2,
        "scraping.requests_per_10_min": 600,
        "subforum.min_read_role": "provisional",
        "subforum.min_thread_role": "provisional",
        "subforum.min_reply_role": "provisional",
        "subforum.post_rate_limit": None,
        "subforum.post_rate_limit_by_role": {},
        "subforum.thread_rate_limit": None,
        "subforum.images": "off",
        "subforum.links": "members_only",
        "subforum.edit_window_minutes": 30,
        "subforum.hold_posts": "first_n_provisional",
    }
    assert {k: s.default for k, s in registry.REGISTRY.items()} == expected


@pytest.mark.parametrize(
    "settings",
    [
        {"no.such.key": 1},
        {"subforum.images": "sometimes"},
        {"subforum.min_read_role": "wizard"},
        {"subforum.post_rate_limit": {"count": 0, "window_hours": 24}},
        {"subforum.post_rate_limit": {"count": 1}},
        {"subforum.post_rate_limit_by_role": {"wizard": None}},
        {"billing.lapse_grace_days": 10},  # site-only key
    ],
)
def test_subforum_settings_are_validated(seeded, settings):
    with pytest.raises(ValidationError):
        SubForum.objects.create(name="X", slug="x", settings=settings)


def test_site_setting_is_validated(owner):
    with pytest.raises(ValidationError):
        SiteSetting.objects.create(key="subforum.images", value="off", updated_by=owner)  # sub-forum-only key
    with pytest.raises(ValidationError):
        SiteSetting.objects.create(key="billing.lapse_grace_days", value="two weeks", updated_by=owner)


def test_fallback_order(owner, general):
    assert general.setting("provisional.held_posts") == 5
    set_site_setting(owner, "provisional.held_posts", 3)
    assert general.setting("provisional.held_posts") == 3
    general.settings["provisional.held_posts"] = 1
    assert general.setting("provisional.held_posts") == 1


def test_only_owner_sets_site_settings_and_it_is_audited(owner, make_user):
    with pytest.raises(PermissionDenied):
        set_site_setting(make_user("admin"), "billing.lapse_grace_days", 21)
    set_site_setting(owner, "billing.lapse_grace_days", 21)
    assert registry.site_value("billing.lapse_grace_days") == 21
    entry = AuditEntry.objects.get(action="site_setting.write")
    assert entry.payload == {"key": "billing.lapse_grace_days", "old": None, "new": 21}


def test_seed_creates_roles_owner_and_subforums(seeded):
    assert list(Role.objects.order_by("-rank").values_list("name", "rank")) == [
        ("owner", 70), ("admin", 60), ("moderator", 50), ("tenured", 40),
        ("full", 30), ("provisional", 20), ("guest", 10),
    ]
    owner = User.objects.get(email="owner@example.test")
    assert roles.is_owner(owner) and owner.status == User.Status.ACTIVE
    assert list(SubForum.objects.values_list("slug", flat=True)) == [
        "guest-lobby", "general-discussion", "serious-discussion", "seminars",
    ]
    lobby = SubForum.objects.get(slug="guest-lobby")
    assert [lobby.setting(f"subforum.min_{k}_role") for k in ("read", "thread", "reply")] == ["guest"] * 3
    assert lobby.setting("subforum.post_rate_limit") is None
    assert lobby.setting("subforum.links") == "off"
    assert SubForum.objects.get(slug="general-discussion").setting("subforum.post_rate_limit") is None
    assert SubForum.objects.get(slug="serious-discussion").setting("subforum.post_rate_limit") == {
        "count": 1, "window_hours": 24,
    }
    assert SubForum.objects.get(slug="seminars").setting("subforum.post_rate_limit") == {
        "count": 1, "window_hours": 168,
    }


def test_seed_is_idempotent_and_keeps_changes(seeded):
    general = SubForum.objects.get(slug="general-discussion")
    general.settings = {"subforum.images": "inline"}
    general.save()
    counts = (Role.objects.count(), User.objects.count(), RoleAssignment.objects.count(), SubForum.objects.count())
    call_command("seed", stdout=io.StringIO())
    assert counts == (
        Role.objects.count(), User.objects.count(), RoleAssignment.objects.count(), SubForum.objects.count()
    )
    general.refresh_from_db()
    assert general.settings == {"subforum.images": "inline"}


def test_seed_without_owner_credentials_fails(db, monkeypatch):
    from django.core.management.base import CommandError

    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    with pytest.raises(CommandError):
        call_command("seed", stdout=io.StringIO())


def test_identity_fields_are_encrypted_at_rest(make_user):
    from django.db import connection

    from accounts.models import IdentityRecord

    member = make_user("guest")
    IdentityRecord.objects.create(user=member, real_name="Ada Example", phone="+1 555 0100")
    with connection.cursor() as cursor:
        cursor.execute("SELECT real_name, phone FROM accounts_identityrecord WHERE user_id = %s", [member.pk])
        stored = cursor.fetchone()
    assert "Ada" not in stored[0] and "555" not in stored[1]
    assert IdentityRecord.objects.get(user=member).real_name == "Ada Example"
