"""Rule 21 and the Markdown allow-list, links and images in docs/DESIGN.md."""

import pytest

from boards.rendering import render_post
from tests.factories import make_post, make_thread


@pytest.fixture
def render(make_user, general):
    def go(source, role="full", subforum=None, links=None, images=None):
        sf = subforum or general
        if links:
            sf.settings["subforum.links"] = links
        if images:
            sf.settings["subforum.images"] = images
        sf.save()
        author = make_user(role)
        post = make_post(make_thread(sf, author), author)
        post.body_source = source
        return render_post(post).html

    return go


@pytest.mark.parametrize("source, fragment", [
    ("**bold**", "<strong>bold</strong>"),
    ("*italic*", "<em>italic</em>"),
    ("~~gone~~", "<s>gone</s>"),
    ("- one\n- two", "<ul>"),
    ("1. one\n2. two", "<ol>"),
    ("> said", "<blockquote>"),
    ("`code`", "<code>code</code>"),
    ("```\nblock\n```", "<pre><code>block"),
    ("---", "<hr />"),
    ("first\nsecond", "first\nsecond"),
    ("# Big", "<h3>Big</h3>"),
    ("## Smaller", "<h4>Smaller</h4>"),
])
def test_allowed_elements_render(render, source, fragment):
    assert fragment in render(source)


def test_third_level_headings_stay_text(render):
    html = render("### Deep")
    assert "<h5>" not in html and "<h3>" not in html and "### Deep" in html


def test_tables_are_not_parsed(render):
    assert "<table" not in render("| a | b |\n|---|---|\n| 1 | 2 |")


def test_raw_html_is_shown_as_text(render):
    html = render('<script>alert(1)</script> <b onclick="x">hi</b>')
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;b onclick" in html


def test_internal_links_always_work(render):
    for links in ("off", "full_and_above", "on"):
        assert '<a href="/t/1/">here</a>' in render("[here](/t/1/)", role="provisional", links=links)


@pytest.mark.parametrize("links, role, clickable", [
    ("off", "owner", False),
    ("full_and_above", "full", True),
    ("full_and_above", "provisional", False),
    ("full_and_above", "guest", False),
    ("on", "guest", True),
])
def test_outside_links_follow_setting_and_role(render, links, role, clickable):
    html = render("[site](https://example.org/page) and https://example.net", role=role, links=links)
    if clickable:
        assert '<a href="https://example.org/page" rel="nofollow noopener noreferrer">site</a>' in html
        assert 'href="https://example.net" rel="nofollow noopener noreferrer"' in html
    else:
        assert "<a" not in html
        assert "site (https://example.org/page)" in html and "https://example.net" in html


def test_dangerous_link_schemes_are_never_links(render):
    html = render("[x](javascript:alert(1)) [y](data:text/html,hi)", links="on")
    assert 'href="javascript' not in html and 'href="data' not in html


def test_image_addresses_from_other_sites_become_links(render):
    html = render("![a cat](https://cats.example/cat.png)", links="on")
    assert "<img" not in html
    assert '<a href="https://cats.example/cat.png" rel="nofollow noopener noreferrer">a cat</a>' in html
    plain = render("![a cat](https://cats.example/cat.png)", links="off")
    assert "<img" not in plain and "<a" not in plain


def test_mentions_link_to_profiles_but_not_in_code(make_user, render):
    target = make_user("full")
    html = render(f"hello @{target.slug} and `@{target.slug}`")
    assert f'<a href="/members/{target.slug}/" class="mention">@{target.slug}</a>' in html
    assert f"<code>@{target.slug}</code>" in html


def test_invited_and_unknown_members_are_not_mentions(make_user, render):
    invited = make_user(None, status="invited")
    html = render(f"@{invited.slug} @nobody-here")
    assert 'class="mention"' not in html
