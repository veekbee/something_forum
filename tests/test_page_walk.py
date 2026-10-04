"""Definition of done for build step 7: every page type renders with the shared layout, checked by
walking the URL patterns a signed-in Owner can reach."""

import re

from django.test import Client
from django.urls import get_resolver, reverse
from django.urls.resolvers import URLResolver

from boards import messages, services
from tests.factories import enrol_totp, make_thread

SKIP_PREFIXES = ("accounts/", "staff/admin/", "billing/stripe/webhook/", "billing/pay/", "billing/manage/",
                 "billing/ban/", "invitations/accept/")


def _routes(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _routes(pattern.url_patterns, prefix + str(pattern.pattern))
        else:
            yield prefix + str(pattern.pattern), pattern.name


def test_every_page_an_owner_reaches_has_the_shared_layout(owner, make_user, general):
    enrol_totp(owner)
    member = make_user("full")
    thread = make_thread(general, member, title="A walked thread")
    post = services.reply(member, thread, "walked words")
    conversation, _ = messages.start(owner, [member], "Walk", "hello")
    values = {
        "slug": member.slug, "pk": thread.pk, "user_pk": member.pk, "name": "icon-192", "key": "custom_emoji",
        "token": "not-a-token", "how": "archive", "what": "follow", "step": "approve", "action": "note",
        "type_": "held", "decision": "release",
    }
    by_name = {
        "subforum": {"slug": general.slug}, "new_thread": {"slug": general.slug}, "legal": {"slug": "privacy"},
        "post_link": {"pk": post.pk}, "edit_post": {"pk": post.pk}, "delete_post": {"pk": post.pk},
        "post_revisions": {"pk": post.pk}, "report_post": {"pk": post.pk}, "conversation": {"pk": conversation.pk},
    }
    client = Client()
    client.force_login(owner)
    rendered, failures = [], []
    for route, name in _routes(get_resolver().url_patterns):
        if route.startswith(SKIP_PREFIXES) or not name:
            continue
        params = re.findall(r"<(?:\w+:)?(\w+)>", route)
        kwargs = by_name.get(name) or {p: values[p] for p in params}
        url = reverse(name, kwargs=kwargs or None)
        response = client.get(url)
        if response.status_code >= 500:
            failures.append(f"{url}: {response.status_code}")
            continue
        if response.status_code != 200 or not response.get("Content-Type", "").startswith("text/html"):
            continue
        page = response.content.decode()
        if '<header class="masthead">' not in page or ("<html" in page and "</html>" not in page):
            failures.append(f"{url}: no shared layout")
        standalone = name in ("offline", "legal")
        if not standalone and '<footer class="site">' not in page:
            failures.append(f"{url}: no footer")
        rendered.append(name)
    assert failures == []
    # The walk reached the main page types.
    for name in ("home", "subforum", "thread", "conversation", "member_profile", "queue", "staff_member",
                 "audit_log", "feed", "site_settings", "trace_watermark", "billing", "extras", "invitations",
                 "sessions", "emoji_list", "search", "notifications", "rap_sheet", "edit_post", "offline", "legal"):
        assert name in rendered, name
