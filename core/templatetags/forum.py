from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.filter
def dollars(cents):
    """1000 -> "$10.00"."""
    return f"${(cents or 0) / 100:,.2f}"
