"""Build step 4 part 4: the per-member view, audited DM reads and DM search, the audit log view and
the Mod feedback feed (rules 8, 29, 37 and 38)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import IdentityRecord
from audit.models import AuditEntry
from boards import messages, services, visibility
from moderation import feed, reports
from moderation import services as moderation
from moderation.services import grant_dm_access
from tests.factories import enrol_totp, grant, make_post, make_thread, sponsor


@pytest.fixture
def login(client):
    def go(user):
        enrol_totp(user)
        client.force_login(user)
        return client

    return go


# --- per-member view -----------------------------------------------------------------------


def test_moderator_tier_leaves_out_dms_payment_sessions_identity_and_blocks(login, make_user, general):
    member = make_user("full")
    IdentityRecord.objects.create(user=member, real_name="Ada Realname", submitted_at=timezone.now())
    messages.start(member, [make_user("full")], "Private chat", "hello")
    client = login(make_user("moderator"))
    page = client.get(f"/staff/members/{member.slug}/").content.decode()
    for hidden in ("Direct messages", "Payment", "Sessions", "Identity details", "Blocks", "Private chat"):
        assert hidden not in page
    assert "Moderation history" in page and "Sponsorship" in page


def test_admin_tier_shows_everything_and_identity_on_request(login, make_user):
    member = make_user("full")
    IdentityRecord.objects.create(user=member, real_name="Ada Realname", submitted_at=timezone.now())
    messages.start(member, [make_user("full")], "Private chat", "hello")
    client = login(make_user("admin"))
    page = client.get(f"/staff/members/{member.slug}/").content.decode()
    for shown in ("Direct messages", "Payment", "Sessions", "Blocks", "Private chat", "Show identity details"):
        assert shown in page
    assert "Ada Realname" not in page
    assert not AuditEntry.objects.filter(action="identity.reveal").exists()  # opening the view is not audited
    page = client.post(f"/staff/members/{member.slug}/", {"reveal": "1"}).content.decode()
    assert "Ada Realname" in page
    entry = AuditEntry.objects.get(action="identity.reveal")
    assert entry.target_id == str(member.pk) and "Ada" not in str(entry.payload)


def test_moderator_sees_posts_only_in_their_sub_forums(login, make_user, general, serious):
    member = make_user("full")
    mine = make_post(make_thread(general, member), member, is_held=True)
    theirs = make_post(make_thread(serious, member), member)
    mod = make_user("tenured")
    grant(mod, "moderator", scope_subforum=general)
    page = login(mod).get(f"/staff/members/{member.slug}/")
    shown = list(page.context["page"].object_list)
    assert mine in shown and theirs not in shown


def test_members_cannot_open_the_staff_view(login, make_user):
    assert login(make_user("tenured")).get(f"/staff/members/{make_user('full').slug}/").status_code == 403


# --- audited reads -------------------------------------------------------------------------


def test_admin_dm_read_writes_one_entry(login, make_user):
    a, b = make_user("full"), make_user("full")
    thread, _ = messages.start(a, [b], "", "between us")
    login(make_user("admin")).get(f"/messages/{thread.pk}/")
    assert AuditEntry.objects.filter(action="dm.read", target_id=str(thread.pk)).count() == 1


def test_search_showing_dm_text_writes_one_entry(login, make_user, general):
    a, b = make_user("full"), make_user("full")
    first, _ = messages.start(a, [b], "", "the word quince")
    second, _ = messages.start(b, [a], "", "more quince talk")
    forum = make_post(make_thread(general, a), a)
    forum.body_source = "quince jam"
    forum.save()
    client = login(make_user("admin"))
    client.get("/search/?q=quince")
    entry = AuditEntry.objects.get(action="dm.search_read")
    assert sorted(entry.payload["threads"]) == sorted([first.pk, second.pk])
    assert "quince" not in str(entry.payload)


def test_forum_only_search_writes_nothing(login, make_user, general):
    a = make_user("full")
    post = make_post(make_thread(general, a), a)
    post.body_source = "quince"
    post.save()
    login(make_user("admin")).get("/search/?q=quince")
    assert not AuditEntry.objects.filter(action="dm.search_read").exists()


def test_dm_search_scope(make_user):
    a, b, c = make_user("full"), make_user("full"), make_user("full")
    ab, ab_post = messages.start(a, [b], "", "apricot")
    bc, bc_post = messages.start(b, [c], "", "apricot")
    for post in (ab_post, bc_post):
        post.body_source = "apricot"
        post.save()
    assert set(visibility.search_posts(make_user("admin"), "apricot")) == {ab_post, bc_post}
    assert not visibility.search_posts(a, "apricot").exists()  # members' search covers no DMs
    mod = make_user("moderator")
    assert not visibility.search_posts(mod, "apricot").exists()
    grant_dm_access(make_user("admin"), mod, [a], case_note="Report", expires_at=timezone.now() + timedelta(days=1))
    assert set(visibility.search_posts(mod, "apricot")) == {ab_post}


def test_no_audit_payload_holds_a_real_name_or_dm_text(login, make_user, general):
    member = make_user("full")
    IdentityRecord.objects.create(user=member, real_name="Zebulon Secretname", submitted_at=timezone.now())
    thread, post = messages.start(member, [make_user("full")], "", "a very private sentence")
    admin = make_user("admin")
    client = login(admin)
    client.post(f"/staff/members/{member.slug}/", {"reveal": "1"})
    client.get(f"/messages/{thread.pk}/")
    client.get("/search/?q=private")
    report, _ = reports.report(thread.participants.exclude(user=member).first().user, post, "personal_attack")
    reports.resolve(admin, report, "no_action")
    for entry in AuditEntry.objects.all():
        text = str(entry.payload)
        assert "Zebulon" not in text and "Secretname" not in text and "private sentence" not in text


# --- audit log view ------------------------------------------------------------------------


def test_audit_log_is_for_admins_and_owners(login, make_user):
    assert login(make_user("moderator")).get("/staff/audit/").status_code == 403
    assert login(make_user("owner")).get("/staff/audit/").status_code == 200


def test_audit_log_filters(login, make_user):
    admin, other_admin = make_user("admin"), make_user("admin")
    target, bystander = make_user("full"), make_user("full")
    moderation.initiate_action(admin, target, "warning", internal_reason="x")
    moderation.initiate_action(other_admin, bystander, "warning", internal_reason="y")
    client = login(make_user("owner"))
    by_actor = client.get(f"/staff/audit/?actor={admin.slug}").context["page"].object_list
    assert by_actor and all(e.actor == admin for e in by_actor)
    by_member = client.get(f"/staff/audit/?member={target.slug}&action=moderation.initiate").context["page"].object_list
    assert [e.payload["target_user"] for e in by_member] == [target.pk]
    today = timezone.now().date().isoformat()
    assert client.get(f"/staff/audit/?from={today}&to={today}").context["page"].object_list
    assert not client.get("/staff/audit/?from=2000-01-01&to=2000-01-02").context["page"].object_list


# --- the feed ------------------------------------------------------------------------------


def test_feed_shows_notes_escalations_declines_withdrawals_and_sponsor_reviews(make_user, general):
    admin = make_user("admin")
    member = make_user("full")
    moderation.initiate_action(make_user("moderator"), member, "note", internal_reason="watch them")
    declined = moderation.initiate_action(make_user("moderator"), member, "warning", internal_reason="x")
    moderation.decline_action(make_user("moderator"), declined, "not needed")
    withdrawn = moderation.initiate_action(make_user("moderator"), member, "warning", internal_reason="y")
    moderation.withdraw_action(withdrawn.initiated_by, withdrawn)
    post = make_post(make_thread(general, member), member)
    report, _ = reports.report(make_user("full"), post, "spam")
    reports.escalate(make_user("moderator"), report, "unsure")
    reports.resolve(admin, report, "no_action")
    banned = make_user("provisional")
    sponsor(make_user("full"), banned)
    moderation.initiate_action(admin, banned, "ban", internal_reason="z")
    from moderation.models import SponsorReview

    moderation.decide_sponsor_review(admin, SponsorReview.objects.get(), "no_action")
    kinds = {item.kind for item in feed.items(make_user("moderator"))}
    assert kinds == {"note", "declined", "withdrawn", "escalated", "resolved", "sponsor_review"}


def test_routine_approvals_and_releases_stay_out(make_user, general):
    action = moderation.initiate_action(make_user("moderator"), make_user("full"), "warning", internal_reason="x")
    moderation.approve_action(make_user("moderator"), action)
    held = services.reply(make_user("provisional"), make_thread(general, make_user("full")), "pending")
    services.release_post(make_user("moderator"), held)
    assert feed.items(make_user("admin")) == []


def test_moderators_never_see_dm_items(make_user):
    a, b = make_user("full"), make_user("full")
    _, post = messages.start(a, [b], "", "rude")
    report, _ = reports.report(b, post, "personal_attack")
    admin = make_user("admin")
    report.escalated_by, report.escalation_note, report.status = admin, "check", "escalated"
    report.save()
    assert any(i.obj == report for i in feed.items(make_user("owner")))
    assert not any(i.obj == report for i in feed.items(make_user("moderator")))


def test_moderators_see_their_sub_forums_and_member_items(make_user, general, serious):
    mod = make_user("tenured")
    grant(mod, "moderator", scope_subforum=general)
    member = make_user("full")
    elsewhere = moderation.initiate_action(make_user("admin"), member, "suspension", internal_reason="x",
                                           scope_subforum=serious)
    pending = moderation.initiate_action(make_user("moderator"), member, "suspension", internal_reason="x",
                                         scope_subforum=serious)
    moderation.decline_action(make_user("admin"), pending, "no")
    about_member = moderation.initiate_action(make_user("admin"), member, "note", internal_reason="member item")
    shown = [i.obj for i in feed.items(mod)]
    assert about_member in shown and pending not in shown and elsewhere not in shown


def test_feed_page_is_for_staff(login, make_user):
    assert login(make_user("full")).get("/staff/feed/").status_code == 403
    assert login(make_user("moderator")).get("/staff/feed/").status_code == 200
