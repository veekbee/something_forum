"""Build step 4 part 2: the moderation queue, reports and automatic flags (rules 33 to 35)."""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from audit.models import AuditEntry
from boards import messages, services
from core.models import Notification
from core.permissions import can
from core.services import set_site_setting
from moderation import flags, queue, reports
from moderation import services as moderation
from moderation.models import Report
from sponsorship import promotions
from tests.factories import enrol_totp, grant, make_post, make_thread, sponsor


@pytest.fixture(autouse=True)
def clear_cache(db):
    cache.clear()


@pytest.fixture
def scoped_mod(make_user):
    def make(subforum):
        mod = make_user("tenured")
        grant(mod, "moderator", scope_subforum=subforum)
        return mod

    return make


def _types(actor):
    return {(item.type, item.obj.pk) for item in queue.items(actor)}


# --- who sees what -------------------------------------------------------------------------


def test_post_items_go_to_that_sub_forums_moderators(make_user, general, serious, scoped_mod):
    author = make_user("provisional")
    held = services.reply(author, make_thread(general, make_user("full")), "pending")
    full = make_user("full")
    post = make_post(make_thread(general, full), full)
    report, _ = reports.report(make_user("full"), post, "spam")
    in_scope, out_of_scope = scoped_mod(general), scoped_mod(serious)
    for viewer in (in_scope, make_user("moderator"), make_user("admin"), make_user("owner")):
        assert {("held", held.pk), ("report", report.pk)} <= _types(viewer)
    assert _types(out_of_scope) == set()
    assert queue.items(make_user("tenured")) == []


def test_member_reports_go_to_global_moderators(make_user, general, scoped_mod):
    report, _ = reports.report(make_user("full"), make_user("full"), "personal_attack", "a pattern")
    assert ("report", report.pk) in _types(make_user("moderator"))
    assert ("report", report.pk) in _types(make_user("admin"))
    assert ("report", report.pk) not in _types(scoped_mod(general))


def test_dm_reports_go_to_admins_and_owners_only(make_user):
    a, b = make_user("full"), make_user("full")
    thread, post = messages.start(b, [a], "", "something nasty")
    report, _ = reports.report(a, post, "personal_attack")
    assert report.kind == "dm"
    assert ("report", report.pk) not in _types(make_user("moderator"))
    assert ("report", report.pk) in _types(make_user("owner"))
    assert not can(make_user("moderator"), "report.view", report)


def test_pending_actions_go_to_those_who_may_approve(make_user):
    target = make_user("full")
    warning = moderation.initiate_action(make_user("moderator"), target, "warning", internal_reason="x")
    ban = moderation.initiate_action(make_user("moderator"), target, "ban", internal_reason="x")
    other_mod = make_user("moderator")
    assert ("action", warning.pk) in _types(other_mod) and ("action", ban.pk) not in _types(other_mod)
    assert ("action", ban.pk) in _types(make_user("admin"))
    assert ("action", warning.pk) not in _types(warning.initiated_by)


def test_promotions_and_sponsor_reviews(make_user, general):
    member = make_user("provisional", granted_at=timezone.now() - timedelta(days=91))
    thread = make_thread(general, member)
    for _ in range(25):
        make_post(thread, member)
    promotion = promotions.recommend(make_user("full"), member)
    assert ("promotion", promotion.pk) in _types(make_user("moderator"))
    promotions.review(make_user("moderator"), promotion)
    assert ("promotion", promotion.pk) not in _types(make_user("moderator"))
    assert ("promotion", promotion.pk) in _types(make_user("admin"))

    banned = make_user("provisional")
    sponsor(make_user("full"), banned)
    moderation.initiate_action(make_user("admin"), banned, "ban", internal_reason="x")
    from moderation.models import SponsorReview

    review = SponsorReview.objects.get()
    assert ("sponsor_review", review.pk) in _types(make_user("owner"))
    assert ("sponsor_review", review.pk) not in _types(make_user("moderator"))


def test_oldest_first_and_filters(make_user, general):
    full = make_user("full")
    old = make_post(make_thread(general, full), full)
    old_report, _ = reports.report(make_user("full"), old, "spam")
    Report.objects.filter(pk=old_report.pk).update(created_at=timezone.now() - timedelta(days=2))
    held = services.reply(make_user("provisional"), make_thread(general, full), "pending")
    items = queue.items(make_user("admin"))
    assert [i.obj.pk for i in items][:2] == [old_report.pk, held.pk]
    assert {i.type for i in queue.items(make_user("admin"), only="held")} == {"held"}


# --- handling ------------------------------------------------------------------------------


def test_a_second_action_on_a_handled_item_changes_nothing(make_user, general):
    full = make_user("full")
    report, _ = reports.report(make_user("full"), make_post(make_thread(general, full), full), "spam")
    first, second = make_user("moderator"), make_user("moderator")
    reports.resolve(first, report, "no_action")
    with pytest.raises(PermissionDenied, match="already handled"):
        reports.resolve(second, report, "action_taken")
    report.refresh_from_db()
    assert (report.handled_by, report.outcome) == (first, "no_action")


def test_hiding_needs_a_preset_reason_and_tells_the_author(make_user, general):
    author = make_user("full")
    post = make_post(make_thread(general, author), author)
    report, _ = reports.report(make_user("full"), post, "spam")
    mod = make_user("moderator")
    with pytest.raises(ValidationError):
        reports.resolve(mod, report, "", hide_reason="because")
    with pytest.raises(ValidationError):
        reports.resolve(mod, report, "", hide_reason="other")
    reports.resolve(mod, report, "", hide_reason="spam")
    post.refresh_from_db()
    report.refresh_from_db()
    assert post.deleted_at is not None and post.delete_reason == "Spam" and report.outcome == "action_taken"
    notice = Notification.objects.get(recipient=author, kind="post.hidden")
    assert notice.payload["reason"] == "Spam"


def test_staff_deleting_a_post_is_hiding(make_user, general):
    author = make_user("full")
    post = make_post(make_thread(general, author), author)
    with pytest.raises(ValidationError):
        services.delete_post(make_user("moderator"), post)
    services.delete_post(make_user("moderator"), post, "other", "Breaks the sub-forum's rules")
    post.refresh_from_db()
    assert post.delete_reason == "Other: Breaks the sub-forum's rules"


def test_removed_post_stays_in_the_authors_history(make_user, general):
    from boards import visibility

    author = make_user("full")
    post = make_post(make_thread(general, author), author)
    services.delete_post(make_user("moderator"), post, "spam")
    assert post in visibility.post_history(author, author)
    own = make_post(make_thread(general, author), author)
    services.delete_post(author, own)
    assert own not in visibility.post_history(author, author)


def test_escalation(make_user, general, owner):
    full = make_user("full")
    reporter = make_user("full")
    report, _ = reports.report(reporter, make_post(make_thread(general, full), full), "personal_attack")
    mod = make_user("moderator")
    with pytest.raises(ValidationError):
        reports.escalate(mod, report, "")
    reports.escalate(mod, report, "Involves a Moderator's friend")
    assert Notification.objects.filter(recipient=owner, kind="queue.escalated").exists()
    assert ("report", report.pk) in _types(make_user("moderator"))  # still visible to Moderators
    reports.add_note(make_user("moderator"), report, "Seen this before")
    with pytest.raises(PermissionDenied):
        reports.resolve(make_user("moderator"), report, "no_action")
    reports.resolve(make_user("admin"), report, "action_taken")
    assert Notification.objects.filter(recipient=mod, kind="queue.escalation_resolved").exists()
    assert Notification.objects.get(recipient=reporter, kind="report.outcome").payload["outcome"] == "action_taken"


# --- reports -------------------------------------------------------------------------------


def test_guests_report_too(make_user, lobby):
    full = make_user("full")
    post = make_post(make_thread(lobby, full), full)
    _, created = reports.report(make_user("guest"), post, "spam")
    assert created


def test_daily_limit_and_duplicates(make_user, general, owner):
    set_site_setting(owner, "reports.max_per_member_per_day", 2)
    reporter, author = make_user("full"), make_user("full")
    thread = make_thread(general, author)
    first = make_post(thread, author)
    report, created = reports.report(reporter, first, "spam")
    again, created_again = reports.report(reporter, first, "off_topic")
    assert created and not created_again and again == report
    reports.report(reporter, make_post(thread, author), "spam")
    with pytest.raises(PermissionDenied):
        reports.report(reporter, make_post(thread, author), "spam")


def test_reported_member_never_learns_who(make_user, general, client):
    author, reporter = make_user("full"), make_user("full")
    post = make_post(make_thread(general, author), author)
    report, _ = reports.report(reporter, post, "spam")
    reports.resolve(make_user("moderator"), report, "", hide_reason="spam")
    assert not can(author, "report.view", report)
    for notice in Notification.objects.filter(recipient=author):
        assert reporter.pk not in notice.payload.values() and reporter.display_name not in str(notice.payload)
    enrol_totp(author)
    client.force_login(author)
    assert client.get("/staff/queue/").status_code == 403


def test_cannot_report_own_post_or_dms_one_is_not_in(make_user, general):
    author = make_user("full")
    post = make_post(make_thread(general, author), author)
    with pytest.raises(PermissionDenied):
        reports.report(author, post, "spam")
    a, b = make_user("full"), make_user("full")
    _, dm_post = messages.start(a, [b], "", "hi")
    with pytest.raises(PermissionDenied):
        reports.report(make_user("admin"), dm_post, "spam")


def test_report_pages(client, make_user, general):
    author, reporter = make_user("full"), make_user("full")
    post = make_post(make_thread(general, author), author)
    enrol_totp(reporter)
    client.force_login(reporter)
    assert client.get(f"/report/post/{post.pk}/").status_code == 200
    response = client.post(f"/report/post/{post.pk}/", {"reason": "spam", "note": "ads"})
    assert b"Thank you" in response.content
    assert Report.objects.get().note == "ads"


# --- flags ---------------------------------------------------------------------------------


def test_rate_limit_refusals_flag_at_the_threshold(make_user, serious):
    member = make_user("full")
    thread = make_thread(serious, make_user("full"))
    services.reply(member, thread, "one a day")
    for attempt in range(1, 4):
        with pytest.raises(PermissionDenied):
            services.reply(member, thread, "another")
        flagged = Report.objects.filter(kind="flag_rate_limit", user=member).exists()
        assert flagged is (attempt == 3)
    assert Report.objects.get(kind="flag_rate_limit").details["subforum"] == serious.pk
    assert ("flag", Report.objects.get().pk) in _types(make_user("moderator"))


def test_rapid_deletions_flag_at_the_threshold(make_user, general):
    member = make_user("full")
    thread = make_thread(general, member)
    for count in range(1, 6):
        services.delete_post(member, make_post(thread, member))
        assert Report.objects.filter(kind="flag_rapid_deletion").exists() is (count == 5)


def test_one_open_flag_per_kind(make_user, general):
    member = make_user("full")
    thread = make_thread(general, member)
    for _ in range(7):
        services.delete_post(member, make_post(thread, member))
    flag = Report.objects.get(kind="flag_rapid_deletion")
    assert flag.details["repeats"] == 2


def test_request_rate_flag_makes_the_account_read_only_until_resolved(make_user, general, owner, client):
    set_site_setting(owner, "scraping.requests_per_10_min", 5)
    member = make_user("full")
    enrol_totp(member)
    client.force_login(member)
    for _ in range(5):
        client.get("/")
    assert not Report.objects.filter(kind="flag_request_rate").exists()
    client.get("/")
    flag = Report.objects.get(kind="flag_request_rate", user=member)
    assert AuditEntry.objects.filter(action="flag.raise").exists()
    thread = make_thread(general, make_user("full"))
    assert not can(member, "thread.reply", thread)
    assert can(member, "thread.read", thread)
    reports.resolve(make_user("moderator"), flag, "no_action")
    assert can(member, "thread.reply", thread)


def test_queue_page_renders_every_item_type(client, make_user, general):
    admin = make_user("admin")
    enrol_totp(admin)
    full = make_user("full")
    services.reply(make_user("provisional"), make_thread(general, full), "held")
    reports.report(make_user("full"), make_post(make_thread(general, full), full), "spam")
    reports.report(make_user("full"), full, "personal_attack")
    flags.raise_flag(full, Report.Kind.FLAG_RAPID_DELETION, {"deletions": 5})
    moderation.initiate_action(make_user("moderator"), full, "warning", internal_reason="x")
    client.force_login(admin)
    page = client.get("/staff/queue/").content.decode()
    for text in ("Held posts", "Reports", "Automatic flags", "Actions awaiting approval"):
        assert text in page
    assert client.get("/staff/queue/?type=held").status_code == 200
