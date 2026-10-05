"""Partial-page updates (docs/DESIGN.md, Partial-page updates; rules 77 and 78).

Every HTMX-enhanced action is a plain form first: without scripts it posts and redirects as before.
An HTMX request gets a fragment instead. Refusals and rate limits are shown inline, in the words the
full page would use, by retargeting the response to the action's error slot; the result is announced
to screen readers through the page's live region (the "announce" event, see static/js/forum-htmx.js)."""

import json

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.html import format_html


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def fragment(request, template, context=None, announce=""):
    response = HttpResponse(render_to_string(template, context or {}, request=request))
    if announce:
        response["HX-Trigger"] = json.dumps({"announce": announce})
    return response


def html(content, announce=""):
    response = HttpResponse(content)
    if announce:
        response["HX-Trigger"] = json.dumps({"announce": announce})
    return response


def refusal(message, target):
    """The reason an action was refused, shown inline in the element `target` (a CSS selector)."""
    response = HttpResponse(format_html('<p class="errors" role="alert">{}</p>', message))
    response["HX-Retarget"] = target
    response["HX-Reswap"] = "innerHTML"
    return response


def sign_in_redirect(request):
    """A fragment request from a signed-out or ended session: the browser goes to sign-in (rule 78)."""
    from urllib.parse import urlsplit

    from django.conf import settings
    from django.shortcuts import resolve_url
    from django.utils.http import urlencode

    current = urlsplit(request.headers.get("HX-Current-URL", "")).path or "/"
    response = HttpResponse("Signed out. Please sign in again.", status=401, content_type="text/plain")
    response["HX-Redirect"] = f"{resolve_url(settings.LOGIN_URL)}?{urlencode({'next': current})}"
    return response
