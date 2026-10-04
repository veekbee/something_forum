"""Build step 5 part 5: deleted-post placeholders, Moderator redaction and the Rap Sheet (rules 26,
47 and 48)."""

import pytest

from audit.models import AuditEntry
from boards import messages, services
from boards.models import SubForum
from moderation import permanent
from moderation import services as moderation
from tests.factories import enrol_totp, grant, make_post, make_thread


@pytest.fixture
def reader(client, make_user):
    user = make_user("provisional")
    enrol_totp(user)
    client.force_login(user)
    return user


# --- placeholders --------------------------------------------------------------------------


def test_deleted_posts_leave_a_placeholder_naming_who(client, reader, make_user, general):
    author = make_user("full")
    thread = make_thread(general, author)
    own = services.reply(author, thread, "my own words")
    services.delete_post(author, own)
    hidden = services.reply(author, thread, "something nasty")
    mod = make_user("moderator")
    services.delete_post(mod, hidden, "personal_attack")
    page = client.get(f"/t/{thread.pk}/").content.decode()
    assert "Deleted by the author." in page
    assert f"Deleted by {mod.display_name}." in page
    assert "my own words" not in page and "something nasty" not in page


def test_admins_still_see_the_text(client, make_user, general):
    author, admin = make_user("full"), make_user("admin")
    thread = make_thread(general, author)
    services.delete_post(author, services.reply(author, thread, "still here for Admins"))
    enrol_totp(admin)
    client.force_login(admin)
    assert b"still here for Admins" in client.get(f"/t/{thread.pk}/").content


def test_rejected_posts_leave_nothing(client, reader, make_user, general):
    thread = make_thread(general, make_user("full"))
    make_post(thread, thread.author)
    held = services.reply(make_user("provisional"), thread, "never published")
    services.reject_post(make_user("moderator"), held, "no")
    page = client.get(f"/t/{thread.pk}/").content.decode()
    assert "Deleted by" not in page and "never published" not in page


# --- redaction -----------------------------------------------------------------------------


def test_moderators_redact_graveyard_threads_from_their_sub_forums(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author)
    post = services.reply(author, thread, "private address here")
    services.send_to_graveyard(make_user("admin"), thread, "doxxing")
    mod = make_user("tenured")
    grant(mod, "moderator", scope_subforum=general)
    services.edit_post(mod, post, "[address removed]")
    assert post.revisions.filter(is_redaction=True, edited_by=mod).exists()
    assert AuditEntry.objects.filter(action="post.redact", actor=mod).exists()


def test_moderators_elsewhere_cannot(make_user, general):
    from django.core.exceptions import PermissionDenied

    author = make_user("full")
    thread = make_thread(general, author)
    post = services.reply(author, thread, "text")
    services.send_to_graveyard(make_user("admin"), thread, "x")
    mod = make_user("tenured")
    grant(mod, "moderator", scope_subforum=SubForum.objects.get(slug="seminars"))
    with pytest.raises(PermissionDenied):
        services.edit_post(mod, post, "[removed]")


# --- the Rap Sheet -------------------------------------------------------------------------


def test_profile_links_to_the_rap_sheet(client, reader, make_user):
    member = make_user("full")
    moderation.initiate_action(make_user("admin"), member, "warning", internal_reason="x", public_summary="Rude")
    assert f"/members/{member.slug}/rap-sheet/".encode() in client.get(f"/members/{member.slug}/").content


def test_rap_sheet_lists_every_public_action_newest_first(client, reader, make_user, owner):
    member = make_user("full")
    admin = make_user("admin")
    moderation.initiate_action(admin, member, "warning", internal_reason="x", public_summary="First")
    moderation.initiate_action(admin, member, "note", internal_reason="private note")
    ban = moderation.initiate_action(admin, member, "ban", internal_reason="x", public_summary="Second")
    moderation.lift_ban(owner, ban, "")
    permanent.impose(owner, member, "x", "Third")
    page = client.get(f"/members/{member.slug}/rap-sheet/").content.decode()
    assert page.index("Third") < page.index("Second") < page.index("First")
    assert "private note" not in page and "Permanent Ban" in page and "Lifted" in page


def test_links_pass_the_readers_permissions(client, reader, make_user, general):
    tenured_only = SubForum.objects.create(name="Inner", slug="inner", settings={"subforum.min_read_role": "tenured"})
    member = make_user("tenured")
    admin = make_user("admin")
    visible = make_post(make_thread(general, member), member)
    hidden_from_reader = make_post(make_thread(tenured_only, member), member)
    removed = make_post(make_thread(general, member), member)
    moderation.initiate_action(admin, member, "warning", internal_reason="x", related_post=visible)
    moderation.initiate_action(admin, member, "warning", internal_reason="x", related_post=hidden_from_reader)
    moderation.initiate_action(admin, member, "warning", internal_reason="x", related_post=removed)
    services.delete_post(admin, removed, "spam")
    _, dm_post = messages.start(member, [make_user("full")], "", "rude message")
    moderation.initiate_action(admin, member, "warning", internal_reason="x", related_post=dm_post)
    entries = client.get(f"/members/{member.slug}/rap-sheet/").context["entries"]
    by_post = {e["action"].related_post_id: e for e in entries}
    assert by_post[visible.pk]["link"] == f"/p/{visible.pk}/"
    assert by_post[hidden_from_reader.pk]["link"] is None
    assert by_post[removed.pk]["link"] is None and "removed by staff" in by_post[removed.pk]["where"]
    assert by_post[dm_post.pk]["link"] is None and by_post[dm_post.pk]["where"] == "in a private message"


def test_guests_do_not_see_rap_sheets(client, make_user):
    guest = make_user("guest")
    enrol_totp(guest)
    client.force_login(guest)
    assert client.get(f"/members/{make_user('full').slug}/rap-sheet/").status_code == 404
