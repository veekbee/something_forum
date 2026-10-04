"""Read positions and unread in bold (docs/DESIGN.md, Read positions; rule 74)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from boards import messages, reading, services
from boards.models import Thread, ThreadRead
from tests.factories import enrol_totp, make_thread


def signed_in(user):
    enrol_totp(user)
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def setting(make_user, general):
    reader, author = make_user("full"), make_user("full")
    thread = make_thread(general, author, title="A conversation about reading")
    services.reply(author, thread, "first words")
    return reader, author, thread


def _bold_in(page, title):
    return 'unread" href=' in page and title in page[page.index('unread" href='):page.index('unread" href=') + 400]


def test_opening_a_thread_marks_it_read_and_new_posts_make_it_unread(setting, general):
    reader, author, thread = setting
    client = signed_in(reader)
    listing = client.get(f"/f/{general.slug}/").content.decode()
    assert _bold_in(listing, thread.title)  # never opened, recent
    client.get(f"/t/{thread.pk}/")
    assert ThreadRead.objects.filter(user=reader, thread=thread).exists()
    assert not _bold_in(client.get(f"/f/{general.slug}/").content.decode(), thread.title)
    Thread.objects.filter(pk=thread.pk).update(last_post_at=timezone.now() + timedelta(seconds=5))
    assert _bold_in(client.get(f"/f/{general.slug}/").content.decode(), thread.title)
    index = client.get("/").content.decode()
    assert 'class="title forum-name unread"' in index


def test_never_opened_threads_are_unread_only_within_the_window(setting):
    reader, _, thread = setting
    assert reading.unread(reader, Thread.objects.filter(pk=thread.pk)).exists()
    Thread.objects.filter(pk=thread.pk).update(last_post_at=timezone.now() - timedelta(days=31))
    assert not reading.unread(reader, Thread.objects.filter(pk=thread.pk)).exists()


def test_held_posts_never_make_a_thread_unread(setting, make_user):
    reader, _, thread = setting
    reading.mark_read(reader, thread)
    held = services.reply(make_user("provisional"), thread, "waiting for review")
    assert held.is_held
    assert not reading.unread(reader, Thread.objects.filter(pk=thread.pk)).exists()


def test_mark_all_read_in_a_sub_forum_and_everywhere(setting, general, serious, make_user):
    reader, author, thread = setting
    other = make_thread(serious, author, title="Elsewhere")
    services.reply(author, other, "words")
    client = signed_in(reader)
    client.post(f"/f/{general.slug}/read/")
    assert not reading.unread(reader, Thread.objects.filter(pk=thread.pk)).exists()
    assert reading.unread(reader, Thread.objects.filter(pk=other.pk)).exists()
    client.post("/read/")
    assert not reading.unread(reader, Thread.objects.filter(pk__in=[thread.pk, other.pk])).exists()


def test_old_read_positions_are_pruned(setting):
    from django.core.management import call_command

    reader, _, thread = setting
    reading.mark_read(reader, thread)
    call_command("prune_read_positions", stdout=__import__("io").StringIO())
    assert ThreadRead.objects.filter(user=reader).exists()
    Thread.objects.filter(pk=thread.pk).update(last_post_at=timezone.now() - timedelta(days=366))
    call_command("prune_read_positions", stdout=__import__("io").StringIO())
    assert not ThreadRead.objects.filter(user=reader).exists()


def test_read_positions_are_private_and_go_with_the_account(setting, make_user):
    from django.contrib import admin

    reader, _, thread = setting
    reading.mark_read(reader, thread)
    assert not admin.site.is_registered(ThreadRead)
    # The reader never posted in the thread, so nothing on their staff view could name it.
    page = signed_in(make_user("admin")).get(f"/staff/members/{reader.slug}/").content.decode()
    assert thread.title not in page
    lone = make_user("full")
    reading.mark_read(lone, thread)
    lone.role_assignments.all().delete()
    lone.delete()
    assert not ThreadRead.objects.filter(user_id=lone.pk).exists()


def test_conversations_keep_their_own_read_position(make_user):
    a, b = make_user("full"), make_user("full")
    conversation, _ = messages.start(a, [b], "Hi", "hello")
    reading.mark_read(b, conversation)
    signed_in(b).get(f"/messages/{conversation.pk}/")
    assert not ThreadRead.objects.exists()
