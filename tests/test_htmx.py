"""Build step 8: partial-page updates with HTMX (docs/DESIGN.md, Partial-page updates; rules 77, 78)."""

import json
import re
from pathlib import Path

from django.conf import settings
from django.test import Client

from boards import services
from boards.models import ThreadParticipant
from tests.factories import enrol_totp, make_thread

HX = {"HTTP_HX_REQUEST": "true", "HTTP_HX_CURRENT_URL": "http://testserver/f/general-discussion/"}
STATIC = Path(settings.BASE_DIR) / "static" / "js"


def signed_in(user):
    enrol_totp(user)
    client = Client()
    client.force_login(user)
    return client


def announced(response):
    return json.loads(response["HX-Trigger"])["announce"]


# --- the library and its configuration ---------------------------------------------------------


def test_htmx_is_self_hosted_with_its_licence():
    assert (STATIC / "htmx" / "htmx.min.js").stat().st_size > 10_000
    assert "Zero-Clause BSD" in (STATIC / "htmx" / "LICENSE.txt").read_text()


def test_configuration_turns_off_eval_scripts_indicator_styles_and_history(make_user):
    page = signed_in(make_user("full")).get("/").content.decode()
    config = json.loads(re.search(r'<meta name="htmx-config" content=\'([^\']+)\'>', page).group(1))
    assert config["allowEval"] is False and config["allowScriptTags"] is False
    assert config["includeIndicatorStyles"] is False and config["historyCacheSize"] == 0
    assert '<p id="live" class="vh" aria-live="polite"></p>' in page
    assert "/static/js/htmx/htmx.min.js" in page and "/static/js/forum-htmx.js" in page


def test_no_evaluated_htmx_attributes_in_templates():
    templates = Path(settings.BASE_DIR) / "templates"
    for path in templates.rglob("*.html"):
        text = path.read_text()
        assert not re.search(r"\bhx-on|\bhx-vars|js:", text), path.name


def test_htmx_is_not_loaded_for_visitors(client, seeded):
    assert "htmx.min.js" not in client.get("/accounts/login/").content.decode()


# --- signed out (rule 78) -----------------------------------------------------------------------


def test_signed_out_fragment_request_redirects_to_sign_in(client, seeded, general):
    response = client.post(f"/f/{general.slug}/read/", **HX)
    assert response.status_code == 401
    assert response["HX-Redirect"].startswith("/accounts/login/?next=%2Ff%2Fgeneral-discussion%2F")


def test_ended_session_fragment_request_redirects_to_sign_in(make_user, general):
    from accounts.models import UserSession

    member = make_user("full")
    client = signed_in(member)
    client.get("/")
    UserSession.objects.filter(user=member).update(revoke_reason="signed_out_by_member",
                                                   revoked_at=__import__("django.utils.timezone", fromlist=["x"]).now())
    response = client.post(f"/f/{general.slug}/read/", **HX)
    assert response.status_code == 401 and response["HX-Redirect"].startswith("/accounts/login/")


# --- follow -----------------------------------------------------------------------------------


def test_follow_plain_form_still_redirects(make_user, general):
    member = make_user("full")
    thread = make_thread(general, make_user("full"))
    response = signed_in(member).post(f"/t/{thread.pk}/follow/")
    assert response.status_code == 302 and ThreadParticipant.objects.filter(thread=thread, user=member).exists()


def test_follow_fragment_swaps_the_button_and_announces(make_user, general):
    member = make_user("full")
    thread = make_thread(general, make_user("full"))
    client = signed_in(member)
    response = client.post(f"/t/{thread.pk}/follow/", **HX)
    body = response.content.decode()
    assert response.status_code == 200 and body.lstrip().startswith("{#") is False
    assert '<form id="follow-form"' in body and ">Unfollow</button>" in body and "<html" not in body
    assert announced(response) == "Following"
    response = client.post(f"/t/{thread.pk}/follow/", **HX)
    assert ">Follow</button>" in response.content.decode() and announced(response) == "No longer following"


def test_follow_refusal_is_shown_inline(make_user, general, monkeypatch):
    from django.core.exceptions import PermissionDenied

    def refuse(*args, **kwargs):
        raise PermissionDenied("you cannot follow this thread")

    monkeypatch.setattr(services, "set_following", refuse)
    thread = make_thread(general, make_user("full"))
    response = signed_in(make_user("full")).post(f"/t/{thread.pk}/follow/", **HX)
    assert response["HX-Retarget"] == "#follow-errors" and response["HX-Reswap"] == "innerHTML"
    assert response.content.decode() == '<p class="errors" role="alert">you cannot follow this thread</p>'


# --- mark all read ----------------------------------------------------------------------------


def test_mark_all_read_fragment_turns_titles_plain(make_user, general):
    member, author = make_user("full"), make_user("full")
    thread = make_thread(general, author, title="Unread thread")
    services.reply(author, thread, "words")
    client = signed_in(member)
    assert 'thread-title unread"' in client.get(f"/f/{general.slug}/").content.decode()
    response = client.post(f"/f/{general.slug}/read/", {"page": "1"}, **HX)
    body = response.content.decode()
    assert '<div id="thread-list" data-focus>' in body and "Unread thread" in body
    assert "unread" not in body and announced(response) == "Marked all read"
    index = client.post("/read/", **HX).content.decode()
    assert '<div id="forum-lists" data-focus>' in index and "forum-name unread" not in index


def test_mark_all_read_plain_form_still_redirects(make_user, general):
    response = signed_in(make_user("full")).post(f"/f/{general.slug}/read/")
    assert response.status_code == 302 and response["Location"] == f"/f/{general.slug}/"


def test_fragments_count_toward_the_request_limit(make_user, owner, general):
    from core.services import set_site_setting
    from moderation.models import Report

    set_site_setting(owner, "scraping.requests_per_10_min", 2)
    client = signed_in(make_user("full"))
    for _ in range(3):
        client.post(f"/f/{general.slug}/read/", {"page": "1"}, **HX)
    assert Report.objects.filter(kind="flag_request_rate").exists()


def test_service_worker_never_caches_fragments(client, seeded):
    worker = client.get("/sw.js").content.decode()
    assert 'if (request.method !== "GET") return;' in worker
    assert "SHELL.includes(url.pathname)" in worker and "caches.match(OFFLINE)" in worker
    assert "cache.put" not in worker
