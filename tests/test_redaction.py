"""A staff edit and a redaction are distinct, everywhere (rules 26 and 56)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from audit.models import AuditEntry
from boards import services
from boards.models import SubForum
from core.permissions import can
from moderation import services as moderation
from tests.factories import enrol_totp, grant, make_thread


@pytest.fixture
def live_post(make_user, general):
    author = make_user("full")
    return services.reply(author, make_thread(general, author), "my words, with a slur")


@pytest.fixture
def general_mod(make_user, general):
    mod = make_user("tenured")
    grant(mod, "moderator", scope_subforum=general)
    return mod


def _page(client, user, url):
    enrol_totp(user)
    client.force_login(user)
    return client.get(url).content


def test_staff_edit_needs_no_reason_and_shows_as_an_edit(client, live_post, general_mod, make_user):
    services.edit_post(general_mod, live_post, "my words, with a fixed link")
    revision = live_post.revisions.latest("pk")
    assert not revision.is_redaction and revision.redaction_reason == ""
    assert AuditEntry.objects.filter(action="post.edit", actor=general_mod).exists()
    page = _page(client, make_user("provisional"), f"/t/{live_post.thread_id}/")
    assert b"edited by staff" in page and b"redacted by staff" not in page


def test_redaction_in_a_live_sub_forum(client, live_post, general_mod, make_user):
    services.edit_post(general_mod, live_post, "my words, [removed]", redact=True, redaction_reason="personal_attack")
    revision = live_post.revisions.latest("pk")
    assert revision.is_redaction and revision.redaction_reason == "Personal attack"
    assert AuditEntry.objects.filter(action="post.redact", actor=general_mod).exists()
    assert b"redacted by staff" in _page(client, make_user("provisional"), f"/t/{live_post.thread_id}/")


@pytest.mark.parametrize("key,note", [("", ""), ("not_a_reason", ""), ("other", "")])
def test_redaction_needs_a_preset_reason(live_post, general_mod, key, note):
    with pytest.raises(ValidationError):
        services.edit_post(general_mod, live_post, "[removed]", redact=True, redaction_reason=key, redaction_note=note)
    assert not live_post.revisions.exists()


def test_other_reason_takes_a_sentence(live_post, general_mod):
    services.edit_post(general_mod, live_post, "[removed]", redact=True, redaction_reason="other",
                       redaction_note="Copied a private letter")
    assert live_post.revisions.latest("pk").redaction_reason == "Other: Copied a private letter"


def test_moderators_redact_only_where_they_moderate(live_post, make_user):
    elsewhere = make_user("tenured")
    grant(elsewhere, "moderator", scope_subforum=SubForum.objects.get(slug="seminars"))
    assert not can(elsewhere, "post.redact", live_post)
    assert can(make_user("admin"), "post.redact", live_post)
    with pytest.raises(PermissionDenied):
        services.edit_post(elsewhere, live_post, "[removed]", redact=True, redaction_reason="spam")


def test_authors_and_members_cannot_redact(live_post, make_user):
    assert not can(live_post.author, "post.redact", live_post)
    assert not can(make_user("full"), "post.redact", live_post)
    with pytest.raises(PermissionDenied):
        services.edit_post(live_post.author, live_post, "[removed]", redact=True, redaction_reason="spam")


def test_graveyard_changes_are_always_redactions_with_a_reason(live_post, make_user):
    admin = make_user("admin")
    services.send_to_graveyard(admin, live_post.thread, "abuse")
    live_post.refresh_from_db()
    with pytest.raises(ValidationError):
        services.edit_post(admin, live_post, "[removed]")
    services.edit_post(admin, live_post, "[removed]", redaction_reason="spam")
    assert live_post.revisions.latest("pk").is_redaction


def test_redaction_linked_from_a_rap_sheet_entry(client, live_post, general_mod, make_user):
    admin = make_user("admin")
    warning = moderation.initiate_action(admin, live_post.author, "warning", internal_reason="slur",
                                         public_summary="Used a slur")
    other_member = moderation.initiate_action(admin, make_user("full"), "warning", internal_reason="x",
                                              public_summary="Something else")
    with pytest.raises(ValidationError):
        services.edit_post(general_mod, live_post, "[removed]", redact=True, redaction_reason="personal_attack",
                           rap_sheet_action=other_member)
    with pytest.raises(ValidationError):
        services.edit_post(general_mod, live_post, "tidied", rap_sheet_action=warning)
    services.edit_post(general_mod, live_post, "[removed]", redact=True, redaction_reason="personal_attack",
                       rap_sheet_action=warning)
    warning.refresh_from_db()
    assert warning.related_post == live_post
    page = _page(client, make_user("provisional"), f"/members/{live_post.author.slug}/rap-sheet/")
    assert f"/p/{live_post.pk}/".encode() in page


def test_owner_purges_a_redacted_live_post_but_not_an_edited_one(live_post, general_mod, make_user, general):
    owner = make_user("owner")
    services.edit_post(general_mod, live_post, "tidied")
    assert not can(owner, "post.purge_revisions", live_post)
    services.edit_post(general_mod, live_post, "[removed]", redact=True, redaction_reason="spam")
    assert services.purge_revisions(owner, live_post) == 2


def test_the_editor_offers_the_choice(client, live_post, general_mod):
    enrol_totp(general_mod)
    client.force_login(general_mod)
    url = f"/p/{live_post.pk}/edit/"
    page = client.get(url)
    assert page.status_code == 200 and b'value="redact"' in page.content
    client.post(url, {"body": "[removed]", "mode": "redact", "reason_key": "spam"})
    assert live_post.revisions.latest("pk").is_redaction
    client.post(url, {"body": "[removed], tidied", "mode": "edit"})
    assert not live_post.revisions.latest("pk").is_redaction
    client.post(url, {"body": "again", "mode": "redact", "reason_key": ""})
    assert live_post.revisions.latest("pk").body_source == "[removed], tidied"


def test_authors_see_no_redaction_choice(client, live_post):
    enrol_totp(live_post.author)
    client.force_login(live_post.author)
    assert b'value="redact"' not in client.get(f"/p/{live_post.pk}/edit/").content


def test_staff_do_not_redact_their_own_posts(general_mod, general):
    own = services.reply(general_mod, make_thread(general, general_mod), "my own words")
    assert can(general_mod, "post.edit", own)
    assert not can(general_mod, "post.redact", own)
