"""Per-session watermarking (rules 21 and 61)."""

import re

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client

from accounts.models import UserSession
from audit.models import AuditEntry
from boards import services
from boards.models import Post
from core import watermark
from core.services import set_site_setting
from tests.factories import enrol_totp, make_thread

ZW = "[​⁠]"
BODY = re.compile(r'<div class="body">(.*?)</div>', re.S)


def signed_in(user):
    enrol_totp(user)
    client = Client()
    client.force_login(user)
    return client


def _marks(text):
    return watermark.find(text)


@pytest.fixture
def thread(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author, title="A plain title")
    code = " ".join(f"code{i}" for i in range(30))
    services.reply(author, thread, " ".join(f"word{i}" for i in range(30)) + f"\n\n```\n{code}\n```")
    return thread


def test_each_session_gets_its_own_mark_in_post_bodies(thread, make_user):
    reader = make_user("full")
    first, second = signed_in(reader), Client()
    second.force_login(reader)
    page_one, page_two = first.get(f"/t/{thread.pk}/").content.decode(), second.get(f"/t/{thread.pk}/").content.decode()
    seeds = {s.watermark_seed for s in UserSession.objects.filter(user=reader)}
    found_one, found_two = _marks(page_one), _marks(page_two)
    assert len(found_one) == 1 and len(found_two) == 1 and found_one != found_two
    assert {f"{found_one[0][0]:08x}", f"{found_two[0][0]:08x}"} == seeds
    assert watermark.day_date(found_one[0][1]) == __import__("django.utils.timezone", fromlist=["x"]).localdate()


def test_marks_only_in_bodies_and_never_in_code(thread, make_user):
    page = signed_in(make_user("full")).get(f"/t/{thread.pk}/").content.decode()
    outside = BODY.sub("", page)
    assert not re.search(ZW, outside)
    body = BODY.search(page).group(1)
    assert len(re.findall(f"{ZW}{{56}}", body)) == 3  # after word 1, then every 12 words
    code = re.search(r"<pre>(.*?)</pre>", body, re.S).group(1)
    assert not re.search(ZW, code) and "code0 code1" in code


def test_dm_messages_are_marked(make_user):
    from boards import messages

    a, b = make_user("full"), make_user("full")
    conversation, _ = messages.start(a, [b], "Plans", "meet at the usual place")
    page = signed_in(b).get(f"/messages/{conversation.pk}/").content.decode()
    assert _marks(BODY.search(page).group(1))


def test_marks_are_never_stored_or_indexed(thread, make_user):
    reader = make_user("full")
    signed_in(reader).get(f"/t/{thread.pk}/")
    for post in Post.objects.all():
        assert not re.search(ZW, post.body_source + post.body_html)
    from boards.visibility import search_posts

    assert search_posts(reader, "word7").exists()


def test_submissions_lose_watermark_characters_and_keep_joiners(thread, make_user):
    member = make_user("full")
    client = signed_in(member)
    text = "pasted​⁠text with ‍ and ‌ kept"
    client.post(f"/t/{thread.pk}/reply/", {"body": text})
    post = Post.objects.filter(author=member).get()
    assert post.body_source == "pastedtext with ‍ and ‌ kept"


def test_setting_turns_marking_off(thread, make_user, owner):
    set_site_setting(owner, "watermark.enabled", False)
    assert not re.search(ZW, signed_in(make_user("full")).get(f"/t/{thread.pk}/").content.decode())


@pytest.mark.parametrize("codepoints", ["200B,200D", "200C,2060", "200B", "200B,200B"])
def test_joiners_and_bad_configurations_are_refused(settings, codepoints):
    settings.WATERMARK_CHARS = "".join(chr(int(c, 16)) for c in codepoints.split(","))
    with pytest.raises(ImproperlyConfigured):
        watermark.characters()


def test_round_trip_and_check():
    run = watermark.encode(0xDEADBEEF, 300)
    assert len(run) == 56 and watermark.decode(run) == (0xDEADBEEF, 300)
    flipped = ("⁠" if run[-1] == "​" else "​")
    assert watermark.decode(run[:-1] + flipped) is None


# --- tracing ---------------------------------------------------------------------------------


def test_admins_trace_a_leak_to_the_session_and_it_is_audited(thread, make_user):
    leaker = make_user("full")
    page = signed_in(leaker).get(f"/t/{thread.pk}/").content.decode()
    excerpt = BODY.search(page).group(1)[:400]
    admin_client = signed_in(make_user("admin"))
    result = admin_client.post("/staff/trace/", {"excerpt": excerpt}).content.decode()
    assert leaker.display_name in result
    session = UserSession.objects.get(user=leaker)
    assert f"session {session.pk}" in result
    entry = AuditEntry.objects.get(action="watermark.trace")
    assert entry.payload["sessions"] == [session.pk] and leaker.display_name not in str(entry.payload)


def test_trace_after_the_session_record_is_deleted(thread, make_user):
    leaker = make_user("full")
    page = signed_in(leaker).get(f"/t/{thread.pk}/").content.decode()
    UserSession.objects.filter(user=leaker).delete()
    result = signed_in(make_user("owner")).post("/staff/trace/", {"excerpt": page}).content.decode()
    assert "record has since been deleted" in result and leaker.display_name not in result


@pytest.mark.parametrize("role", ["moderator", "full"])
def test_only_admins_and_owners_trace(make_user, role):
    assert signed_in(make_user(role)).get("/staff/trace/").status_code == 403
