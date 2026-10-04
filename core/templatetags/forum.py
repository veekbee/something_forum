from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.filter
def dollars(cents):
    """1000 -> "$10.00"."""
    return f"${(cents or 0) / 100:,.2f}"


# Generated avatar colours; static/css/site.css has a .avatar-cN class for each, in this order.
_COLOURS = ("#2f5d8a", "#8a4b0f", "#2e6b3a", "#6b2e5f", "#5f5a1e", "#1e5f5a", "#7a2e2e", "#3e3e7a")
_SIZES = (32, 40, 56)


@register.simple_tag
def avatar(user, size=40):
    """The member's avatar: their uploaded image with the extra, otherwise their initials on a
    colour chosen from their profile name."""
    import hashlib

    from django.urls import reverse
    from django.utils.html import format_html

    if getattr(user, "avatar_id", None):
        return format_html('<img class="avatar" src="{}" alt="" width="{}" height="{}">',
                           reverse("attachment", args=[user.avatar_id]), size, size)
    initials = "".join(word[0] for word in (user.display_name or "?").split()[:2]).upper() or "?"
    colour = int(hashlib.sha256(user.slug.encode()).hexdigest(), 16) % len(_COLOURS)
    # Classes rather than an inline style, which the content security policy forbids (rule 64).
    return format_html('<span class="avatar avatar-c{} avatar-s{}" aria-hidden="true">{}</span>',
                       colour, _size_class(size), initials)


def _size_class(size):
    return min(_SIZES, key=lambda s: abs(s - int(size)))


@register.simple_tag(takes_context=True)
def watermarked(context, html):
    """A post body or DM message as stored, with this reader's emoji display (rule 62) and this
    session's watermark (rule 61) applied to the response. The stored HTML is never changed (rule 21)."""
    from django.utils.safestring import mark_safe

    from boards import emoji
    from core import watermark

    request = context.get("request")
    if request is None:
        return mark_safe(html)
    return mark_safe(watermark.for_request(request, emoji.for_request(request, html)))
