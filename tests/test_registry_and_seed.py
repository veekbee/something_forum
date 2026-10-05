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
        "sponsorship.max_open_vouch_requests": 3,
        "sponsorship.review_on_member_ban": True,
        "promotion.full.min_days": 90,
        "promotion.full.min_posts": 25,
        "promotion.tenured.min_days": 90,
        "provisional.held_posts": 5,
        "billing.lapse_grace_days": 14,
        "invitation.expiry_days": 14,
        "invitation.ended_account_deletion_days": 30,
        "billing.ban_reversal_fee_cents": 1000,
        "billing.ban_reversal_fee_multiplier": 2,
        "billing.lapse_restrict_days": 90,
        "billing.renewal_reminder_days": 30,
        "billing.read_only_warning_days": 3,
        "billing.founding_comp_months": 12,
        "billing.founding_comp_warning_days": 30,
        "extras.caption_max_chars": 40,
        "subforum.readable_when_lapsed": True,
        "auth.require_totp": True,
        "retention.audit_years_after_departure": 2,
        "export.link_hours": 24,
        "export.keep_days": 7,
        "export.min_days_between": 7,
        "erasure.deadline_days": 30,
        "erasure.deferral_days": 30,
        "erasure.reminder_days": 7,
        "jobs.daily_hour_utc": 3,
        "scraping.requests_per_10_min": 600,
        "session.concurrency_window_minutes": 30,
        "session.member_lifetime_days": 30,
        "session.member_idle_days": 14,
        "session.staff_lifetime_days": 7,
        "session.staff_idle_days": 1,
        "session.retention_days": 90,
        "watermark.enabled": True,
        "emoji.max_height_px": 128,
        "emoji.max_aspect_ratio": 3,
        "emoji.max_kb": 512,
        "emoji.allow_animated": True,
        "site.name": "Something Forum",
        "legal.reviewed": False,
        "reading.unread_window_days": 30,
        "reading.prune_after_days": 365,
        "dm.max_participants": 8,
        "dm.max_new_conversations_per_day": 10,
        "dm.links": "full_and_above",
        "dm.images": "inline",
        "dm.edit_window_minutes": 1440,
        "reports.max_per_member_per_day": 10,
        "flags.rate_limit_refusals": 3,
        "flags.rate_limit_window_hours": 24,
        "flags.rapid_deletions": 5,
        "flags.rapid_deletion_window_minutes": 60,
        "notifications.max_emails_per_day": 1,
        "subforum.min_read_role": "provisional",
        "subforum.min_thread_role": "provisional",
        "subforum.min_reply_role": "provisional",
        "subforum.post_rate_limit": None,
        "subforum.post_rate_limit_by_role": {},
        "subforum.thread_rate_limit": None,
        "subforum.images": "off",
        "subforum.links": "full_and_above",
        "subforum.max_images_per_post": 4,
        "subforum.max_image_mb": 5,
        "mentions.max_notified_per_post": 10,
        "pagination.posts_per_thread_page": 20,
        "pagination.threads_per_subforum_page": 30,
        "pagination.search_results_per_page": 20,
        "pagination.profile_posts_per_page": 20,
        "subforum.edit_window_minutes": 1440,
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
    assert list(SubForum.objects.values_list("slug", "kind")) == [
        ("guest-lobby", "regular"), ("general-discussion", "regular"), ("serious-discussion", "regular"),
        ("seminars", "regular"), ("thread-graveyard", "graveyard"), ("thread-classics", "classics"),
    ]
    for area in SubForum.objects.exclude(kind="regular"):
        assert area.setting("subforum.min_read_role") == "provisional"
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


def test_links_value_rename_migration(seeded):
    import importlib

    from django.apps import apps

    migration = importlib.import_module("boards.migrations.0003_rename_links_members_only")
    old = SubForum.objects.create(name="Old", slug="old")
    SubForum.objects.filter(pk=old.pk).update(settings={"subforum.links": "members_only"})
    migration.forward(apps, None)
    old.refresh_from_db()
    assert old.settings == {"subforum.links": "full_and_above"}
    assert SubForum.objects.get(slug="guest-lobby").settings["subforum.links"] == "off"


# --- the Owner settings page (rule 55) ---------------------------------------------------------


def _owner_client(owner):
    from django.test import Client

    from tests.factories import enrol_totp

    enrol_totp(owner)
    client = Client()
    client.force_login(owner)
    return client


def test_owner_edits_a_setting_and_it_is_audited(owner):
    from audit.models import AuditEntry
    from core import registry

    client = _owner_client(owner)
    page = client.get("/staff/settings/").content
    assert b"session.retention_days" in page and b"Default: <code>90</code>" in page
    client.post("/staff/settings/", {"key": "session.retention_days", "value": "120"})
    assert registry.site_value("session.retention_days") == 120
    entry = AuditEntry.objects.get(action="site_setting.write")
    assert entry.payload == {"key": "session.retention_days", "old": None, "new": 120}
    assert b"changed" in client.get("/staff/settings/").content


def test_values_are_checked_against_the_registry(owner):
    from core import registry

    client = _owner_client(owner)
    page = client.post("/staff/settings/", {"key": "session.retention_days", "value": "-3"}).content
    assert b"must be a positive integer" in page
    client.post("/staff/settings/", {"key": "dm.links", "value": "sometimes"})
    assert registry.site_value("dm.links") == "full_and_above"
    client.post("/staff/settings/", {"key": "site.name", "value": "Test Commons"})
    assert registry.site_value("site.name") == "Test Commons"
    client.post("/staff/settings/", {"key": "watermark.enabled", "value": "false"})
    assert registry.site_value("watermark.enabled") is False
    assert b"unknown setting" in client.post("/staff/settings/", {"key": "nope", "value": "1"}).content


def test_reset_to_default_is_audited(owner):
    from audit.models import AuditEntry
    from core import registry
    from core.models import SiteSetting

    client = _owner_client(owner)
    client.post("/staff/settings/", {"key": "emoji.max_kb", "value": "256"})
    client.post("/staff/settings/", {"key": "emoji.max_kb", "reset": "1"})
    assert registry.site_value("emoji.max_kb") == 512 and not SiteSetting.objects.filter(key="emoji.max_kb").exists()
    entry = AuditEntry.objects.get(action="site_setting.reset")
    assert entry.payload == {"key": "emoji.max_kb", "old": 256, "new": 512}


def test_only_owners_edit_settings(make_user, owner):
    from django.core.exceptions import PermissionDenied

    from core.services import reset_site_setting

    admin = make_user("admin")
    assert _owner_client(admin).post("/staff/settings/", {"key": "emoji.max_kb", "value": "1"}).status_code == 403
    with pytest.raises(PermissionDenied):
        reset_site_setting(admin, "emoji.max_kb")


def test_subforum_only_settings_are_not_on_the_page(owner):
    client = _owner_client(owner)
    assert b"subforum.links" not in client.get("/staff/settings/").content
    page = client.post("/staff/settings/", {"key": "subforum.links", "value": "on"}).content
    assert b"Not a site-wide setting" in page
