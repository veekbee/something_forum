"""The step 4 corrections (3 Oct 2026): escalating any queue item, limited actions naming several
sub-forums, and starters following their own threads."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from boards import services
from core.models import Notification
from core.permissions import can
from moderation import reports
from moderation import services as moderation
from moderation.models import Report
from sponsorship import promotions
from tests.factories import enrol_totp, grant, make_post, make_thread


@pytest.fixture
def held(make_user, general):
    return services.reply(make_user("provisional"), make_thread(general, make_user("full")), "pending")


# --- escalating any item -------------------------------------------------------------------


def test_escalating_a_held_post(make_user, held, owner):
    mod, other_mod, admin = make_user("moderator"), make_user("moderator"), make_user("admin")
    escalation = reports.escalate_item(mod, held, "Possibly defamatory")
    assert (escalation.kind, escalation.status, escalation.post) == ("escalation", "escalated", held)
    assert Notification.objects.filter(recipient=owner, kind="queue.escalated").exists()
    assert not can(other_mod, "post.moderate", held)        # waits for an Admin or Owner
    assert can(other_mod, "report.view", escalation)          # but stays visible to Moderators
    assert can(admin, "post.moderate", held)
    with pytest.raises(PermissionDenied):
        reports.escalate_item(other_mod, held, "again")
    reports.resolve(admin, escalation, "no_action")
    held.refresh_from_db()
    assert held.is_held                                        # resolving does not release it
    assert can(other_mod, "post.moderate", held)               # handed back to Moderators
    assert Notification.objects.filter(recipient=mod, kind="queue.escalation_resolved").exists()
    assert not Notification.objects.filter(recipient=mod, kind="report.outcome").exists()


def test_escalating_a_pending_action(make_user):
    initiator, mod = make_user("moderator"), make_user("moderator")
    action = moderation.initiate_action(initiator, make_user("full"), "warning", internal_reason="x")
    reports.escalate_item(mod, action, "Conflict of interest")
    assert not can(make_user("moderator"), "moderation.approve", action)
    moderation.approve_action(make_user("admin"), action)


def test_escalating_a_promotion(make_user, general):
    member = make_user("provisional", granted_at=timezone.now() - timedelta(days=91))
    thread = make_thread(general, member)
    for _ in range(25):
        make_post(thread, member)
    promotion = promotions.recommend(make_user("full"), member)
    reports.escalate_item(make_user("moderator"), promotion, "Recommender is their partner")
    with pytest.raises(PermissionDenied):
        promotions.review(make_user("moderator"), promotion)
    promotions.review(make_user("admin"), promotion)


def test_only_moderators_who_can_see_an_item_escalate_it(make_user, held, serious):
    outsider = make_user("tenured")
    grant(outsider, "moderator", scope_subforum=serious)
    for actor in (outsider, make_user("full"), make_user("admin")):
        assert not can(actor, "queue.escalate", held)


def test_escalate_button_in_the_queue(client, make_user, held):
    mod = make_user("moderator")
    enrol_totp(mod)
    client.force_login(mod)
    assert f"/staff/queue/escalate/held/{held.pk}/".encode() in client.get("/staff/queue/").content
    client.post(f"/staff/queue/escalate/held/{held.pk}/", {"note": "Unsure"})
    assert Report.objects.filter(kind="escalation", post=held).exists()
    assert b"Escalated: waiting for an Admin or Owner" in client.get("/staff/queue/").content


# --- several sub-forums --------------------------------------------------------------------


def test_one_action_can_name_several_sub_forums(make_user, general, serious, seminars):
    mod = make_user("tenured")
    grant(mod, "moderator", scope_subforum=general)
    grant(mod, "moderator", scope_subforum=serious)
    approver = make_user("moderator")
    target = make_user("full")
    action = moderation.initiate_action(mod, target, "suspension", internal_reason="x",
                                        scope_subforums=[general, serious])
    moderation.approve_action(approver, action)
    assert not can(target, "subforum.start_thread", general)
    assert not can(target, "subforum.start_thread", serious)
    assert can(target, "subforum.start_thread", seminars)


def test_every_named_sub_forum_must_be_one_they_moderate(make_user, general, serious):
    mod = make_user("tenured")
    grant(mod, "moderator", scope_subforum=general)
    with pytest.raises(PermissionDenied):
        moderation.initiate_action(mod, make_user("full"), "hold", internal_reason="x",
                                   scope_subforums=[general, serious])


def test_record_lists_every_named_sub_forum(client, make_user, general, serious):
    target, viewer = make_user("full"), make_user("full")
    moderation.initiate_action(make_user("admin"), target, "suspension", internal_reason="x",
                               scope_subforums=[general, serious])
    enrol_totp(viewer)
    client.force_login(viewer)
    assert b"Suspension in General Discussion and Serious Discussion" in client.get(f"/members/{target.slug}/").content


# --- following -----------------------------------------------------------------------------


def test_starters_follow_their_threads_and_replying_follows_nothing(make_user, general):
    starter, replier, other = make_user("full"), make_user("full"), make_user("full")
    thread, _ = services.start_thread(starter, general, "Topic", "Opening")
    services.reply(replier, thread, "a reply")
    assert Notification.objects.filter(recipient=starter, kind="thread.reply").count() == 1
    services.reply(other, thread, "another")
    assert not Notification.objects.filter(recipient=replier, kind="thread.reply").exists()
    services.set_following(starter, thread, False)
    Notification.objects.filter(recipient=starter).update(read_at=timezone.now())
    services.reply(other, thread, "after unfollowing")
    assert not Notification.objects.filter(recipient=starter, read_at__isnull=True).exists()
