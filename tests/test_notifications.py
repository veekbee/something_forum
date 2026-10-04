"""Build step 4 part 5: notifications, pointer-only email and following threads (rule 39)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from boards import messages, services
from core.models import Notification, NotificationPreference
from core.notifications import send_digests
from moderation import services as moderation
from tests.factories import enrol_totp, make_post, make_thread


@pytest.fixture
def emails(mailoutbox, django_capture_on_commit_callbacks):
    """Run the code under test with on-commit callbacks executed, so account emails go out."""

    def run(fn):
        with django_capture_on_commit_callbacks(execute=True):
            return fn()

    run.outbox = mailoutbox
    return run


# --- email ---------------------------------------------------------------------------------


def test_account_kinds_always_email_at_once_without_content_or_sender(make_user, emails):
    admin, member = make_user("admin"), make_user("full")
    emails(lambda: moderation.initiate_action(admin, member, "warning", internal_reason="Rude to Sam",
                                              public_summary="Rudeness"))
    [mail] = emails.outbox
    assert mail.to == [member.email]
    text = mail.subject + mail.body
    for secret in ("Rude", "Sam", "warning", admin.display_name, admin.email):
        assert secret not in text
    assert "/notifications/" in mail.body
    assert Notification.objects.get(recipient=member, kind="moderation.action").emailed_at is not None


def test_optional_kinds_are_off_by_default(make_user, general, emails):
    author, target = make_user("full"), make_user("full")
    emails(lambda: services.reply(author, make_thread(general, author), f"hi @{target.slug}"))
    assert emails.outbox == []
    assert send_digests() == 0


def test_chosen_kinds_are_batched_to_one_email_a_day(make_user, general, mailoutbox):
    author, target = make_user("full"), make_user("full")
    NotificationPreference.objects.create(user=target, kind="mention", email=True)
    NotificationPreference.objects.create(user=target, kind="dm", email=True)
    thread = make_thread(general, author)
    services.reply(author, thread, f"one @{target.slug}")
    messages.start(author, [target], "", "a message")
    assert send_digests() == 1
    [mail] = mailoutbox
    assert "a message" not in mail.body and author.display_name not in mail.body
    services.reply(author, make_thread(general, author), f"two @{target.slug}")
    assert send_digests() == 0  # one a day
    assert send_digests(now=timezone.now() + timedelta(days=1, minutes=1)) == 1


def test_read_notifications_are_not_emailed(make_user, general):
    author, target = make_user("full"), make_user("full")
    NotificationPreference.objects.create(user=target, kind="mention", email=True)
    services.reply(author, make_thread(general, author), f"@{target.slug}")
    Notification.objects.filter(recipient=target).update(read_at=timezone.now())
    assert send_digests() == 0


def test_staff_and_other_kinds_are_in_app_only(make_user, emails, owner):
    emails(lambda: moderation.initiate_action(make_user("moderator"), make_user("full"), "note", internal_reason="x"))
    assert emails.outbox == []
    assert Notification.objects.filter(recipient=owner, kind="moderation.note").exists()


def test_digest_command(make_user):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("send_notification_emails", stdout=out)
    assert "Sent 0" in out.getvalue()


# --- the page ------------------------------------------------------------------------------


def test_page_lists_marks_read_and_the_header_counts(client, make_user, general):
    author, target = make_user("full"), make_user("full")
    services.reply(author, make_thread(general, author), f"@{target.slug}")
    enrol_totp(target)
    client.force_login(target)
    assert b"Notifications (1)" in client.get("/").content
    page = client.get("/notifications/").content.decode()
    assert "You were mentioned in a post" in page
    assert b"Notifications (1)" not in client.get("/").content


def test_choosing_email_kinds(client, make_user):
    member = make_user("full")
    enrol_totp(member)
    client.force_login(member)
    client.post("/notifications/settings/", {"mention": "on"})
    chosen = dict(NotificationPreference.objects.filter(user=member).values_list("kind", "email"))
    assert chosen == {"mention": True, "dm": False, "thread.reply": False, "promotion": False}


# --- following threads ---------------------------------------------------------------------


def test_followers_hear_of_replies_once_until_read(make_user, general):
    starter, follower = make_user("full"), make_user("full")
    thread = make_thread(general, starter)
    services.set_following(follower, thread, True)
    services.reply(starter, thread, "one")
    services.reply(starter, thread, "two")
    assert Notification.objects.filter(recipient=follower, kind="thread.reply").count() == 1
    assert not Notification.objects.filter(recipient=starter, kind="thread.reply").exists()  # not followed by default


def test_held_replies_notify_followers_on_release(make_user, general):
    starter, follower = make_user("full"), make_user("full")
    thread = make_thread(general, starter)
    services.set_following(follower, thread, True)
    held = services.reply(make_user("provisional"), thread, "pending")
    assert not Notification.objects.filter(recipient=follower).exists()
    services.release_post(make_user("moderator"), held)
    assert Notification.objects.filter(recipient=follower, kind="thread.reply").exists()


def test_unfollow_and_blocks(make_user, general):
    starter, follower = make_user("full"), make_user("full")
    thread = make_thread(general, starter)
    services.set_following(follower, thread, True)
    messages.block(follower, starter)
    services.reply(starter, thread, "blocked author")
    assert not Notification.objects.filter(recipient=follower).exists()
    services.set_following(follower, thread, False)
    messages.unblock(follower, starter)
    services.reply(starter, thread, "after unfollowing")
    assert not Notification.objects.filter(recipient=follower).exists()


def test_follow_button(client, make_user, general):
    member = make_user("full")
    thread = make_thread(general, make_user("full"))
    make_post(thread, thread.author)
    enrol_totp(member)
    client.force_login(member)
    assert b"Follow</button>" in client.get(f"/t/{thread.pk}/").content
    client.post(f"/t/{thread.pk}/follow/")
    assert thread.participants.filter(user=member).exists()
    assert b"Unfollow</button>" in client.get(f"/t/{thread.pk}/").content


# --- promotion news ------------------------------------------------------------------------


def test_promotion_news(make_user, general):
    from sponsorship import promotions

    member = make_user("full", granted_at=timezone.now() - timedelta(days=91))
    promotions.promote_to_tenured(make_user("admin"), member)
    assert Notification.objects.get(recipient=member, kind="promotion").payload["approved"] is True
