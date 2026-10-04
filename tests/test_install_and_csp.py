"""Build step 6 part 1: the content security policy, what the request limit counts, the legal
pages and home-screen installability (rules 14, 60, 64 and 65)."""

import io
import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from PIL import Image

from django.test import Client

from boards import services
from core.services import set_site_setting
from moderation.models import Report
from tests.factories import enrol_totp, make_thread

TEMPLATES = Path(settings.BASE_DIR) / "templates"


@pytest.fixture
def member_client(client, make_user):
    member = make_user("full")
    enrol_totp(member)
    client.force_login(member)
    client.member = member
    return client


# --- content security policy ------------------------------------------------------------------


def _policy(response):
    policy = response["Content-Security-Policy"]
    assert "script-src 'self'" in policy and "style-src 'self'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    return policy


@pytest.mark.parametrize("path", ["/accounts/login/", "/legal/privacy/", "/offline/", "/manifest.webmanifest",
                                  "/sw.js", "/icons/icon-192.png", "/robots.txt"])
def test_csp_on_public_responses(client, seeded, path):
    _policy(client.get(path))


def test_csp_on_member_pages(member_client, general, make_user):
    thread = make_thread(general, member_client.member)
    services.reply(member_client.member, thread, "hello")
    for path in ["/", f"/f/{general.slug}/", f"/t/{thread.pk}/", f"/members/{member_client.member.slug}/",
                 "/messages/", "/notifications/", "/help/install/", "/billing/"]:
        response = member_client.get(path)
        assert response.status_code == 200, path
        _policy(response)


def test_no_template_uses_inline_script_style_or_handlers():
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text()
        for match in re.finditer(r"<script\b[^>]*>", text):
            if "src=" not in match.group(0):
                offenders.append(f"{path.name}: inline script")
        if re.search(r"\sstyle\s*=", text):
            offenders.append(f"{path.name}: style attribute")
        if re.search(r"<style\b", text):
            offenders.append(f"{path.name}: style element")
        if re.search(r"\son[a-z]+\s*=", text):
            offenders.append(f"{path.name}: event handler attribute")
    assert offenders == []


def test_generated_avatar_uses_classes(member_client):
    page = member_client.get(f"/members/{member_client.member.slug}/").content.decode()
    assert re.search(r'class="avatar avatar-c\d avatar-s56"', page) and "style=" not in page


# --- what the request limit counts ------------------------------------------------------------


def _avatar(user):
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    from boards.models import Attachment

    out = io.BytesIO()
    Image.new("RGB", (8, 8), "#336699").save(out, format="PNG")
    key = default_storage.save(f"test-avatars/{user.slug}.png", ContentFile(out.getvalue()))
    item = Attachment.objects.create(uploader=user, storage_key=key, filename="a.png", mime="image/png",
                                     size_bytes=len(out.getvalue()), width=8, height=8)
    user.avatar = item
    user.save(update_fields=["avatar"])
    return item


def test_only_pages_and_fragments_count(member_client, owner):
    set_site_setting(owner, "scraping.requests_per_10_min", 3)
    image = _avatar(member_client.member)
    for _ in range(10):
        member_client.get("/icons/icon-192.png")
        member_client.get("/manifest.webmanifest")
        member_client.get("/sw.js")
        response = member_client.get(f"/attachments/{image.pk}/")
        assert response.status_code == 200
        b"".join(response.streaming_content)
    member_client.get("/")
    member_client.get("/", HTTP_HX_REQUEST="true")
    member_client.get("/")
    assert not Report.objects.filter(kind="flag_request_rate").exists()
    member_client.get("/")
    assert Report.objects.filter(kind="flag_request_rate", user=member_client.member).exists()


# --- legal pages ------------------------------------------------------------------------------


@pytest.fixture
def unreviewed(seeded):
    from core.models import SiteSetting

    SiteSetting.objects.filter(key="legal.reviewed").delete()


@pytest.mark.parametrize("slug,title", [("member-agreement", b"Member agreement"), ("privacy", b"Privacy notice")])
def test_legal_pages_are_public_drafts_until_reviewed(client, unreviewed, owner, slug, title):
    response = client.get(f"/legal/{slug}/")
    assert response.status_code == 200
    assert title in response.content and b"Draft." in response.content and b"legal review" in response.content
    set_site_setting(owner, "legal.reviewed", True)
    assert b"Draft." not in client.get(f"/legal/{slug}/").content


def test_no_invitations_until_the_legal_text_is_reviewed(member_client, unreviewed, owner):
    from django.core.exceptions import PermissionDenied

    from audit.models import AuditEntry
    from tests.factories import invite

    page = member_client.get("/invitations/").content
    assert b"awaiting legal review" in page
    with pytest.raises(PermissionDenied):
        invite(member_client.member)
    owner_client = Client()
    enrol_totp(owner)
    owner_client.force_login(owner)
    owner_client.post("/staff/settings/", {"key": "legal.reviewed", "value": "true"})
    assert AuditEntry.objects.filter(action="site_setting.write", payload__key="legal.reviewed").exists()
    invitation, _ = invite(member_client.member)
    assert invitation.pk


def test_unknown_legal_page_is_not_found(client, seeded):
    assert client.get("/legal/anything/").status_code == 404


# --- installability ---------------------------------------------------------------------------


def test_manifest_served_without_a_session(client, owner):
    set_site_setting(owner, "site.name", "Test Commons")
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200 and response["Content-Type"] == "application/manifest+json"
    body = json.loads(response.content)
    assert body["name"] == "Test Commons" and body["display"] == "standalone" and body["start_url"] == "/"
    assert {"maskable", "any"} == {icon["purpose"] for icon in body["icons"]}


@pytest.mark.parametrize("name,size", [("icon-192", 192), ("icon-512", 512), ("maskable-512", 512),
                                       ("apple-touch-icon", 180)])
def test_icons_are_placeholder_pngs(client, seeded, name, size):
    response = client.get(f"/icons/{name}.png")
    assert response.status_code == 200 and response["Content-Type"] == "image/png"
    assert Image.open(io.BytesIO(response.content)).size == (size, size)


def test_unknown_icon_is_not_found(client, seeded):
    assert client.get("/icons/other.png").status_code == 404


def test_service_worker_caches_only_the_shell(client, seeded):
    response = client.get("/sw.js")
    assert response.status_code == 200 and response["Content-Type"] == "application/javascript"
    shell = json.loads(re.search(r"const SHELL = (\[.*?\]);", response.content.decode()).group(1))
    assert "/offline/" in shell
    assert all(p.startswith("/static/") or p.startswith("/icons/") or p == "/offline/" for p in shell)


def test_offline_page_has_no_member_content(client, member_client, general):
    services.reply(member_client.member, make_thread(general, member_client.member), "secret words")
    page = member_client.get("/offline/").content
    assert b"You're offline." in page and b"Try again" in page
    assert b"secret words" not in page and member_client.member.display_name.encode() not in page
    assert b"<nav" not in page


def test_install_help_linked_from_the_footer(member_client):
    page = member_client.get("/").content
    assert b'href="/help/install/"' in page and b'rel="manifest"' in page
    assert b"Add Something Forum to your home screen" in member_client.get("/help/install/").content


def test_install_help_needs_a_session(client, seeded):
    assert client.get("/help/install/").status_code == 302


def test_site_name_drives_the_brand_and_authenticator_issuer(member_client, owner):
    from accounts.adapters import MFAAdapter

    set_site_setting(owner, "site.name", "Test Commons")
    assert b'class="brand" href="/">Test Commons</a>' in member_client.get("/").content
    assert MFAAdapter().get_totp_issuer() == "Test Commons"


def test_offline_page_served_without_a_session(client, seeded):
    response = client.get("/offline/")
    assert response.status_code == 200 and b"You're offline." in response.content
