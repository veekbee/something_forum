"""Rules 23 and 24: mentions and quotes."""

import pytest
from django.core.exceptions import ValidationError

from boards import services
from boards.models import PostQuote
from core.models import Notification
from core.services import set_site_setting
from tests.factories import make_thread


def _mentions(user):
    return Notification.objects.filter(recipient=user, kind="mention")


def test_mention_notifies_a_reader(make_user, general):
    author, target = make_user("full"), make_user("full")
    post = services.reply(author, make_thread(general, author), f"hi @{target.slug}")
    assert _mentions(target).get().payload == {"post": post.pk, "thread": post.thread_id}


def test_no_notification_to_someone_who_cannot_read_the_thread(make_user, general):
    author, guest = make_user("full"), make_user("guest")
    services.reply(author, make_thread(general, author), f"hi @{guest.slug}")
    assert not _mentions(guest).exists()


def test_no_notification_for_mentioning_yourself(make_user, general):
    author = make_user("full")
    services.reply(author, make_thread(general, author), f"note to self @{author.slug}")
    assert not _mentions(author).exists()


def test_held_posts_notify_on_release_and_rejected_never(make_user, general):
    member, target, mod = make_user("provisional"), make_user("full"), make_user("moderator")
    thread = make_thread(general, target)
    held = services.reply(member, thread, f"@{target.slug} first")
    assert held.is_held and not _mentions(target).exists()
    services.release_post(mod, held)
    assert _mentions(target).count() == 1
    rejected = services.reply(member, thread, f"@{target.slug} second")
    services.reject_post(mod, rejected, "no")
    assert _mentions(target).count() == 1


def test_mention_cap(make_user, general, owner):
    set_site_setting(owner, "mentions.max_notified_per_post", 2)
    author = make_user("full")
    targets = [make_user("full") for _ in range(3)]
    services.reply(author, make_thread(general, author), " ".join(f"@{t.slug}" for t in targets))
    assert Notification.objects.filter(kind="mention").count() == 2


def test_edit_notifies_only_new_mentions(make_user, general):
    author, first, second = make_user("full"), make_user("full"), make_user("full")
    post = services.reply(author, make_thread(general, author), f"@{first.slug}")
    services.edit_post(author, post, f"@{first.slug} and @{second.slug}")
    assert _mentions(first).count() == 1 and _mentions(second).count() == 1


def test_quote_carries_author_link_and_excerpt(make_user, general):
    author, replier = make_user("full"), make_user("full")
    thread = make_thread(general, author)
    original = services.reply(author, thread, "The original point.")
    reply = services.reply(replier, thread, f"[quote={original.pk}]\nThe original point.\n[/quote]\n\nI agree.")
    assert f'<a href="/p/{original.pk}/">{author.display_name} wrote</a>' in reply.body_html
    assert "The original point." in reply.body_html
    assert PostQuote.objects.filter(quoting_post=reply, quoted_post=original).exists()


def test_quote_from_another_thread_is_refused(make_user, general):
    author = make_user("full")
    elsewhere = services.reply(author, make_thread(general, author), "elsewhere")
    with pytest.raises(ValidationError):
        services.reply(author, make_thread(general, author), f"[quote={elsewhere.pk}]\nelsewhere\n[/quote]")


def test_held_and_rejected_posts_cannot_be_quoted(make_user, general):
    member, author = make_user("provisional"), make_user("full")
    thread = make_thread(general, author)
    held = services.reply(member, thread, "pending")
    with pytest.raises(ValidationError):
        services.reply(author, thread, f"[quote={held.pk}]\npending\n[/quote]")


def test_editing_the_original_rerenders_the_quote_with_a_note(make_user, general):
    author, replier = make_user("full"), make_user("full")
    thread = make_thread(general, author)
    original = services.reply(author, thread, "Version one.")
    reply = services.reply(replier, thread, f"[quote={original.pk}]\nVersion one.\n[/quote]")
    assert "edited since" not in reply.body_html
    services.edit_post(author, original, "Version two.")
    reply.refresh_from_db()
    assert "edited since" in reply.body_html and "Version one." in reply.body_html


def test_deleting_the_original_hides_the_quoted_text(make_user, general):
    author, replier = make_user("full"), make_user("full")
    thread = make_thread(general, author)
    original = services.reply(author, thread, "Something regrettable.")
    reply = services.reply(replier, thread, f"[quote={original.pk}]\nSomething regrettable.\n[/quote]\n\nWow.")
    services.delete_post(author, original)
    reply.refresh_from_db()
    assert "Quoted post deleted" in reply.body_html and "regrettable" not in reply.body_html
    assert "Wow." in reply.body_html
    # Staff can still read the quoted words in the quoting post's source and history.
    assert "regrettable" in reply.body_source

