"""Post rendering: Markdown through the allow-list in docs/DESIGN.md (rules 21 to 24).

A post is rendered once, when it is written or edited, and again only when something it depends on
changes (a post it quotes is edited or deleted). Raw HTML is escaped, never rendered.

- Allowed: paragraphs and line breaks, bold, italic, strikethrough, lists, block quotes, inline code
  and code blocks, horizontal rules, and two heading levels (`#`, `##`) rendered small. Deeper
  headings stay as plain text; tables are not parsed.
- Links inside the forum always work. Outside links follow the sub-forum's links setting and the
  author's role, and carry rel="nofollow noopener noreferrer"; a link that may not be clickable is
  shown as plain text with its address.
- Images: only images uploaded with the post, placed with `![caption](image:N)` when the sub-forum
  shows images inline, or shown beneath the post otherwise. Any other image address becomes an
  ordinary link, so no image is ever loaded from another site.
- Mentions: `@slug` links to the member; the caller decides who is notified.
- Quotes: `[quote=POST_ID]` ... `[/quote]` blocks, from visible posts in the same thread only.
"""

import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from markdown_it import MarkdownIt
from markdown_it.token import Token

from accounts import roles
from core import registry

QUOTE_RE = re.compile(r"^\[quote=(\d+)\][ \t]*\n(.*?)\n\[/quote\][ \t]*$", re.MULTILINE | re.DOTALL)
MENTION_RE = re.compile(r"(?<![\w@/.])@([a-z0-9][a-z0-9-]{0,79})")
PLACEHOLDER_IMAGE_RE = re.compile(r"^image:(\d+)$")
OUTSIDE_REL = "nofollow noopener noreferrer"


def _markdown():
    return MarkdownIt("commonmark", {"html": False, "linkify": True}).enable(["strikethrough", "linkify"])


MD = _markdown()


@dataclass
class Rendered:
    html: str
    mentions: list = field(default_factory=list)
    quoted_ids: list = field(default_factory=list)


def _internal_hosts():
    return {h.lstrip(".").lower() for h in settings.ALLOWED_HOSTS if h and h != "*"}


def is_internal(href):
    parts = urlsplit(href)
    if not parts.scheme and not parts.netloc:
        return True
    if parts.scheme in ("http", "https") and parts.hostname:
        host = parts.hostname.lower()
        return any(host == h or host.endswith("." + h) for h in _internal_hosts())
    return False


def _setting(subforum, key):
    return subforum.setting(key) if subforum is not None else registry.get(key).default


class _Context:
    def __init__(self, post, attachments, collect_mentions):
        self.post = post
        thread = post.thread
        subforum = thread.subforum
        self.links = _setting(subforum, "subforum.links")
        self.images = _setting(subforum, "subforum.images")
        self.author_full = roles.trust_rank(post.author) >= roles.rank_of(roles.FULL)
        self.attachments = list(attachments)
        self.used_images = set()
        self.collect_mentions = collect_mentions
        self.mention_slugs = []

    def outside_clickable(self):
        return self.links == "on" or (self.links == "full_and_above" and self.author_full)


def _text(content):
    token = Token("text", "", 0)
    token.content = content
    return token


def _link_tokens(href, text, rel=None, css=None):
    open_ = Token("link_open", "a", 1)
    open_.attrs = {"href": href}
    if rel:
        open_.attrs["rel"] = rel
    if css:
        open_.attrs["class"] = css
    return [open_, _text(text), Token("link_close", "a", -1)]


def _mention_tokens(content, ctx, users):
    out, last = [], 0
    for match in MENTION_RE.finditer(content):
        user = users.get(match.group(1))
        if user is None:
            continue
        out.append(_text(content[last:match.start()]))
        out.extend(_link_tokens(reverse("member_profile", args=[user.slug]), f"@{user.slug}", css="mention"))
        if ctx.collect_mentions:
            ctx.mention_slugs.append(user.slug)
        last = match.end()
    if not out:
        return [_text(content)]
    out.append(_text(content[last:]))
    return out


def _process_inline(children, ctx, users):
    out, link_stack = [], []
    for token in children:
        if token.type == "link_open":
            href = token.attrGet("href") or ""
            if is_internal(href):
                token.attrs = {"href": href}
                link_stack.append(("keep", href))
                out.append(token)
            elif ctx.outside_clickable():
                token.attrs = {"href": href, "rel": OUTSIDE_REL}
                link_stack.append(("keep", href))
                out.append(token)
            else:
                # Not clickable: keep the link text and show the address after it.
                link_stack.append(("drop", href, len(out)))
        elif token.type == "link_close":
            entry = link_stack.pop()
            if entry[0] == "keep":
                out.append(token)
            else:
                href, start = entry[1], entry[2]
                shown = "".join(t.content for t in out[start:] if t.type == "text")
                if shown.strip() != href:
                    out.append(_text(f" ({href})"))
        elif token.type == "image":
            out.extend(_image(token, ctx))
        elif token.type == "text" and not link_stack:
            out.extend(_mention_tokens(token.content, ctx, users))
        else:
            out.append(token)
    return out


def _image(token, ctx):
    src = token.attrGet("src") or ""
    alt = token.content
    match = PLACEHOLDER_IMAGE_RE.match(src)
    if match:
        index = int(match.group(1)) - 1
        if ctx.images == "inline" and 0 <= index < len(ctx.attachments):
            attachment = ctx.attachments[index]
            ctx.used_images.add(attachment.pk)
            token.attrs = {"src": reverse("attachment", args=[attachment.pk]), "alt": alt, "loading": "lazy"}
            return [token]
        return [_text(alt)] if alt else []
    # An image address from anywhere else is an ordinary link, so nothing is loaded from outside.
    open_ = Token("link_open", "a", 1)
    open_.attrs = {"href": src}
    return _process_inline([open_, _text(alt or src), Token("link_close", "a", -1)], ctx, {})


def _restrict_headings(tokens):
    for i, token in enumerate(tokens):
        if token.type not in ("heading_open", "heading_close"):
            continue
        level = int(token.tag[1])
        if level <= 2:
            token.tag = f"h{level + 2}"
            continue
        # Deeper headings are not allowed: show the line as a paragraph, markers included.
        token.type = "paragraph_open" if token.type == "heading_open" else "paragraph_close"
        if token.type == "paragraph_open":
            inline = tokens[i + 1]
            prefix = ("#" * level) + " "
            if inline.children:
                inline.children.insert(0, _text(prefix))
        token.tag = "p"


def _mentionable_users(source):
    from accounts.models import User

    slugs = set(MENTION_RE.findall(source))
    if not slugs:
        return {}
    users = User.objects.filter(slug__in=slugs).exclude(
        status__in=[User.Status.INVITED, User.Status.REMOVED, User.Status.TOMBSTONE]
    )
    return {u.slug: u for u in users}


def _render_markdown(source, ctx):
    tokens = MD.parse(source, {})
    _restrict_headings(tokens)
    users = _mentionable_users(source)
    for token in tokens:
        if token.type == "inline" and token.children is not None:
            token.children = _process_inline(token.children, ctx, users)
    return MD.renderer.render(tokens, MD.options, {})


def _quote_html(quoted, quoting_post, inner_html):
    if quoted is None or quoted.deleted_at is not None or quoted.is_held or quoted.rejected_at is not None:
        return mark_safe('<blockquote class="quote quote-deleted"><p class="quote-meta">Quoted post deleted</p></blockquote>')
    edited_since = quoted.edited_at is not None and quoted.edited_at > quoting_post.created_at
    note = mark_safe(' <span class="quote-note">· edited since</span>') if edited_since else ""
    return format_html(
        '<blockquote class="quote"><p class="quote-meta"><a href="{}">{} wrote</a>{}</p>{}</blockquote>',
        reverse("post_link", args=[quoted.pk]), quoted.author.display_name, note, inner_html,
    )


def render_post(post, attachments=(), validate_quotes=True, collect_mentions=True):
    """Render post.body_source. With validate_quotes, a quote of anything but a visible post in the
    same thread raises ValidationError; on re-render a vanished original shows as deleted."""
    from boards.models import Post

    ctx = _Context(post, attachments, collect_mentions)
    nonce = secrets.token_hex(8)
    quotes, quoted_ids = {}, []

    def take_quote(match):
        quoted = Post.objects.select_related("author").filter(pk=int(match.group(1))).first()
        if validate_quotes and (
            quoted is None
            or quoted.thread_id != post.thread_id
            or quoted.is_held
            or quoted.rejected_at is not None
            or quoted.deleted_at is not None
        ):
            raise ValidationError("You can only quote visible posts from this thread.")
        inner_ctx = _Context(post, (), collect_mentions=False)
        inner = _render_markdown(QUOTE_RE.sub("", match.group(2)), inner_ctx)
        key = f"QUOTE{nonce}N{len(quotes)}"
        quotes[key] = _quote_html(quoted, post, inner)
        if quoted is not None:
            quoted_ids.append(quoted.pk)
        return key

    source = QUOTE_RE.sub(take_quote, post.body_source)
    html = _render_markdown(source, ctx)
    for key, quote in quotes.items():
        html = html.replace(f"<p>{key}</p>", quote).replace(key, quote)

    remaining = [a for a in ctx.attachments if a.pk not in ctx.used_images]
    if remaining and ctx.images in ("inline", "attachments"):
        css = "post-images" if ctx.images == "inline" else "post-attachments"
        items = "".join(
            str(format_html('<a href="{0}"><img src="{0}" alt="{1}" loading="lazy"></a>',
                            reverse("attachment", args=[a.pk]), a.filename))
            for a in remaining
        )
        html += f'<div class="{css}">{items}</div>\n'

    seen, mentions = set(), []
    users = _mentionable_users(post.body_source)
    for slug in ctx.mention_slugs:
        if slug not in seen:
            seen.add(slug)
            mentions.append(users[slug])
    return Rendered(html=html, mentions=mentions, quoted_ids=list(dict.fromkeys(quoted_ids)))


def quote_source(post):
    """What the Quote button puts in the editor: the post's own words, without its quotes."""
    body = QUOTE_RE.sub("", post.body_source).strip()
    return f"[quote={post.pk}]\n{body}\n[/quote]\n\n"
