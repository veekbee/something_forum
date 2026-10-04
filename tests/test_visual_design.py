"""Build step 7: the visual design pass (docs/DESIGN.md, Visual design; rules 64 and 73)."""

import io
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from PIL import Image

from tests.factories import enrol_totp

STATIC = Path(settings.BASE_DIR) / "static"
CSS = (STATIC / "css" / "forum.css").read_text()


def signed_in(user):
    enrol_totp(user)
    client = Client()
    client.force_login(user)
    return client


# --- fonts (rule 73) ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", ["source-serif-4", "source-sans-3"])
def test_fonts_are_self_hosted_with_their_licence(family):
    folder = STATIC / "fonts" / family
    files = list(folder.glob("*.woff2"))
    assert len(files) == 2
    assert "SIL Open Font License" in (folder / "OFL.txt").read_text()
    for path in files:
        assert f"../fonts/{family}/{path.name}" in CSS


def test_stylesheet_loads_nothing_from_elsewhere():
    assert not re.search(r"url\(\s*['\"]?(https?:|//|data:)", CSS)
    assert "@import" not in CSS


# --- contrast (WCAG 2.2 AA) --------------------------------------------------------------------


def _tokens(block):
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-f]{6})", block))


def _schemes():
    light = _tokens(CSS[CSS.index(":root {"):CSS.index("@media (prefers-color-scheme: dark)")])
    dark = dict(light, **_tokens(CSS[CSS.index("@media (prefers-color-scheme: dark)"):CSS.index("/* --- base")]))
    return {"light": light, "dark": dark}


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


PAIRS = [(fg, bg) for bg in ("bg", "surface", "band") for fg in ("text", "muted", "link", "warn", "ok", "esc")]
PAIRS += [("accent-text", "accent"), ("head-text", "head"), ("head-muted", "head")]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg", PAIRS)
def test_text_pairings_meet_aa(scheme, fg, bg):
    tokens = _schemes()[scheme]
    assert contrast(tokens[fg], tokens[bg]) >= 4.5, f"{scheme}: --{fg} on --{bg}"


def test_generated_avatar_colours_meet_aa():
    from core.templatetags.forum import _COLOURS

    assert len(_COLOURS) == 8
    for index, colour in enumerate(_COLOURS):
        assert f".avatar-c{index} {{ background: {colour}; }}" in CSS
        assert contrast("#fbfaf6", colour) >= 4.5, colour


# --- the shared layout -------------------------------------------------------------------------


def test_masthead_menu_tab_bar_and_footer(make_user):
    member = make_user("full")
    page = signed_in(member).get("/").content.decode()
    assert '<header class="masthead">' in page and 'class="brand"' in page
    assert '<details class="menu"><summary>Menu</summary>' in page
    tabs = page[page.index('<nav class="tabs"'):]
    tabs = tabs[:tabs.index("</nav>")]
    assert [label for label in ("Forum", "Messages", "Notifications", "Profile") if label in tabs] == [
        "Forum", "Messages", "Notifications", "Profile"]
    assert 'href="/" aria-current="page"' in tabs
    assert "Queue" not in page[page.index("<nav aria-label"):page.index("<details")]
    assert '<footer class="site">' in page and 'class="skip" href="#content"' in page


def test_staff_see_the_queue_with_its_count(make_user, general):
    from boards import services
    from tests.factories import make_thread

    services.reply(make_user("provisional"), make_thread(general, make_user("full")), "held for review")
    page = signed_in(make_user("moderator")).get("/").content.decode()
    nav = page[page.index("<nav aria-label"):page.index("<details")]
    assert 'Queue <span class="count">1</span>' in nav
    assert '<div class="group">Staff</div>' in page


def test_account_state_bands(make_user):
    from accounts.models import User

    lapsed = make_user("full", status=User.Status.READ_ONLY)
    assert "Your membership has lapsed" in signed_in(lapsed).get("/").content.decode()


def test_profile_tab_marks_only_your_own_profile(make_user):
    member, other = make_user("full"), make_user("full")
    client = signed_in(member)
    own = client.get(f"/members/{member.slug}/").content.decode()
    assert f'href="/members/{member.slug}/" aria-current="page"' in own
    assert 'aria-current="page"' not in client.get(f"/members/{other.slug}/").content.decode()


# --- wordmark, icons and a supplied logo -------------------------------------------------------


def test_wordmark_is_the_site_name(make_user, owner):
    from core.services import set_site_setting

    set_site_setting(owner, "site.name", "The Reading Room")
    assert '<a class="brand" href="/">The Reading Room</a>' in signed_in(make_user("full")).get("/").content.decode()


def test_icon_initials_follow_the_name(client, owner):
    from django.core.cache import cache

    from core.services import set_site_setting

    first = client.get("/icons/icon-192.png").content
    set_site_setting(owner, "site.name", "The Reading Room")
    cache.clear()
    assert client.get("/icons/icon-192.png").content != first


def test_a_supplied_logo_replaces_wordmark_and_icons(client, make_user, settings, tmp_path):
    from django.core.cache import cache

    logo = tmp_path / "brand"
    logo.mkdir()
    Image.new("RGB", (600, 600), "#123456").save(logo / "icon.png")
    (logo / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
    settings.STATICFILES_DIRS = [*settings.STATICFILES_DIRS, str(tmp_path)]
    settings.SITE_LOGO, settings.SITE_ICON = "brand/logo.svg", "brand/icon.png"
    from django.contrib.staticfiles import finders

    finders.get_finder.cache_clear()
    cache.clear()
    page = signed_in(make_user("full")).get("/").content.decode()
    assert 'class="brand" href="/"><img src="/static/brand/logo.svg"' in page
    icon = Image.open(io.BytesIO(client.get("/icons/icon-512.png").content)).convert("RGB")
    assert icon.getpixel((256, 256)) == (0x12, 0x34, 0x56)
    finders.get_finder.cache_clear()
    cache.clear()


# --- forum, thread, DM and profile pages -------------------------------------------------------


def test_index_is_a_table_that_collapses(make_user):
    page = signed_in(make_user("full")).get("/").content.decode()
    assert '<table class="list forums">' in page and '<th class="n">Threads</th>' in page
    assert 'data-label="threads"' in page and 'class="section-head">Where threads end' in page


def test_thread_page_author_column_and_notes(make_user, general):
    from boards import services
    from tests.factories import make_thread

    author = make_user("full")
    author.caption = "slow reader"
    author.save(update_fields=["caption"])
    thread = make_thread(general, author)
    post = services.reply(author, thread, "a slur here")
    mod = make_user("moderator")
    services.edit_post(mod, post, "[removed]", redact=True, redaction_reason="personal_attack")
    page = signed_in(make_user("full")).get(f"/t/{thread.pk}/").content.decode()
    column = page[page.index('<div class="author">'):]
    column = column[:column.index("</div>")]
    assert "slow reader" in column and "Full · joined" in column and "avatar-s56" in column
    assert '<span class="red">redacted by staff: personal attack</span>' in page
    assert f'<p class="crumbs"><a href="/">Forum</a> › <a href="/f/{general.slug}/">' in page


def test_reply_form_states_the_limit_and_the_next_time(make_user, serious):
    from boards import services
    from tests.factories import make_thread

    member = make_user("full")
    thread = make_thread(serious, make_user("full"))
    client = signed_in(member)
    page = client.get(f"/t/{thread.pk}/").content.decode()
    assert "Serious Discussion allows 1 post per member per day." in page and "You can post here again" not in page
    services.reply(member, thread, "my one post today")
    page = client.get(f"/t/{thread.pk}/").content.decode()
    assert "You can post here again at" in page and "You can't reply until then." in page
    assert "Post reply" not in page


def test_no_limit_line_where_posting_is_unlimited(make_user, general):
    from tests.factories import make_thread

    page = signed_in(make_user("full")).get(f"/t/{make_thread(general, make_user('full')).pk}/").content.decode()
    assert 'class="limit"' not in page and "Post reply" in page


def test_conversation_layout(make_user):
    from boards import messages

    a, b = make_user("full"), make_user("full")
    conversation, _ = messages.start(a, [b], "Plans", "see you there")
    page = signed_in(b).get(f"/messages/{conversation.pk}/").content.decode()
    assert '<p class="dm-notice" id="notice">' in page and '<div class="msg' in page
    assert '<span class="compose-note"><a href="#notice">' in page


def test_profile_head_and_facts(make_user):
    member = make_user("full")
    page = signed_in(member).get(f"/members/{member.slug}/").content.decode()
    assert '<div class="profile-head">' in page and '<dl class="facts">' in page
    assert 'class="section-head">Posts' in page


# --- staff pages -------------------------------------------------------------------------------


def test_queue_summary_table_and_filters(make_user, general):
    from boards import services
    from moderation import reports
    from tests.factories import make_thread

    author = make_user("full")
    held = services.reply(make_user("provisional"), make_thread(general, author), "held words")
    report, _ = reports.report(make_user("full"), author, "spam")
    mod = make_user("moderator")
    reports.escalate(mod, report, "needs an Admin")
    page = signed_in(make_user("admin")).get("/staff/queue/").content.decode()
    assert '<table class="dense">' in page and '<span class="tag esc">escalated</span>' in page
    assert ">All 2</a>" in page and "Held posts 1</a>" in page and "Reports 1</a>" in page
    assert f'<a href="/staff/members/{held.author.slug}/">' in page and 'href="#item-1"' in page and 'id="item-2"' in page
    filtered = signed_in(make_user("admin")).get("/staff/queue/?type=held").content.decode()
    assert 'class="on">Held posts 1</a>' in filtered and "held words" in filtered and "needs an Admin" not in filtered


@pytest.mark.parametrize("path", ["/staff/queue/", "/staff/audit/", "/staff/feed/", "/staff/settings/", "/staff/trace/"])
def test_staff_pages_are_denser(owner, path):
    assert '<main id="content" class="staff">' in signed_in(owner).get(path).content.decode()


def test_member_pages_are_not_dense(make_user):
    assert '<main id="content">' in signed_in(make_user("full")).get("/").content.decode()


# --- allauth's pages and the standalone pages --------------------------------------------------


def test_sign_in_uses_the_site_layout(client, seeded):
    page = client.get("/accounts/login/").content.decode()
    assert '<header class="masthead">' in page and '<main id="content" class="auth">' in page
    assert "Sign In" in page and "Menu:" not in page


def test_password_reset_uses_the_site_layout(client, seeded):
    page = client.get("/accounts/password/reset/").content.decode()
    assert '<main id="content" class="auth">' in page


def test_authenticator_setup_uses_the_site_layout(client, make_user):
    member = make_user("full")
    client.force_login(member)
    response = client.get("/accounts/2fa/totp/activate/", follow=True)  # via re-authentication
    assert response.status_code == 200
    assert '<main id="content" class="auth">' in response.content.decode()


def test_email_change_form_is_hidden_without_an_inline_style():
    source = (Path(settings.BASE_DIR) / "templates" / "account" / "email_change.html").read_text()
    assert "<form hidden" in source and "style=" not in source


@pytest.mark.parametrize("path", ["/offline/", "/legal/privacy/"])
def test_standalone_pages_carry_the_wordmark(client, seeded, path):
    assert '<span class="brand">Something Forum</span>' in client.get(path).content.decode()


# --- accessibility fixes found by axe during step 7 --------------------------------------------


def test_links_in_running_text_are_underlined():
    """WCAG 1.4.1: a link inside text never relies on colour alone."""
    rule = CSS[CSS.index("p a, dd a"):]
    rule = rule[:rule.index("}")]
    assert "text-decoration: underline" in rule and ".body a" in rule


def test_queue_action_column_has_a_label_for_screen_readers(make_user, general):
    from boards import services
    from tests.factories import make_thread

    services.reply(make_user("provisional"), make_thread(general, make_user("full")), "held")
    page = signed_in(make_user("admin")).get("/staff/queue/").content.decode()
    assert '<th><span class="vh">Actions</span></th>' in page
