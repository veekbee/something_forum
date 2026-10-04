from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.filter
def dollars(cents):
    """1000 -> "$10.00"."""
    return f"${(cents or 0) / 100:,.2f}"


_COLOURS = ("#2f5d8a", "#8a4b0f", "#2e6b3a", "#6b2e5f", "#5f5a1e", "#1e5f5a", "#7a2e2e", "#3e3e7a")


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
    colour = _COLOURS[int(hashlib.sha256(user.slug.encode()).hexdigest(), 16) % len(_COLOURS)]
    return format_html('<span class="avatar" style="background:{};width:{}px;height:{}px" aria-hidden="true">{}</span>',
                       colour, size, size, initials)
