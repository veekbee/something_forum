from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core import notifications, registry
from core.models import Notification, NotificationPreference


@require_GET
def robots_txt(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


@require_GET
def notifications_page(request):
    """The member's notifications, newest first. Opening the page marks them read."""
    items = Notification.objects.filter(recipient=request.user).order_by("-created_at")
    page = Paginator(items, registry.site_value("pagination.profile_posts_per_page")).get_page(request.GET.get("page"))
    rows = [{"n": n, "text": notifications.describe(n)[0], "link": notifications.describe(n)[1]} for n in page.object_list]
    Notification.objects.filter(pk__in=[n.pk for n in page.object_list], read_at__isnull=True).update(read_at=timezone.now())
    chosen = set(NotificationPreference.objects.filter(user=request.user, email=True).values_list("kind", flat=True))
    return render(request, "core/notifications.html", {
        "page": page, "rows": rows,
        "optional": [(kind, label, kind in chosen) for kind, label in notifications.OPTIONAL_KINDS.items()],
    })


@require_POST
def notification_settings(request):
    """Email for optional kinds is off unless the member turns it on (rule 39)."""
    for kind in notifications.OPTIONAL_KINDS:
        NotificationPreference.objects.update_or_create(
            user=request.user, kind=kind, defaults={"email": request.POST.get(kind) == "on"}
        )
    return redirect("notifications")


def _parse_setting(text):
    """Values are typed as JSON (numbers, true or false, null, objects); anything that is not JSON
    is taken as text, so a name needs no quotes."""
    import json

    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def site_settings(request):
    """The Owner settings page (rule 55): every site-wide setting with its current value and default,
    which Owners edit or reset; each change is validated against the registry and audited with old
    and new values. Also the date billing launched."""
    import json

    from django.core.exceptions import PermissionDenied, ValidationError

    from audit.models import AuditEntry
    from core import services
    from core.models import SiteSetting
    from core.permissions import can

    decision = can(request.user, "site_setting.write")
    if not decision:
        raise PermissionDenied(decision.reason)
    errors = {}
    if request.method == "POST":
        key = request.POST.get("key", "")
        try:
            setting = registry.get(key)
            if registry.SITE not in setting.scopes:
                raise ValidationError("Not a site-wide setting.")
            if request.POST.get("reset"):
                services.reset_site_setting(request.user, key)
            else:
                services.set_site_setting(request.user, key, _parse_setting(request.POST.get("value", "")))
        except (ValidationError, KeyError, LookupError) as exc:
            errors[key] = " ".join(getattr(exc, "messages", [str(exc)]))
        else:
            return redirect(f"{request.path}#setting-{key}")
    stored = set(SiteSetting.objects.values_list("key", flat=True))
    rows = []
    for s in registry.REGISTRY.values():
        if registry.SITE not in s.scopes:
            continue
        value = registry.site_value(s.key)
        rows.append({"key": s.key, "value": json.dumps(value) if not isinstance(value, str) else value,
                     "default": json.dumps(s.default) if not isinstance(s.default, str) else s.default,
                     "changed": s.key in stored, "status": s.status, "description": s.description,
                     "error": errors.get(s.key, "")})
    launch = AuditEntry.objects.filter(action="billing.launch").order_by("created_at").first()
    shown = {r["key"] for r in rows}
    page_errors = [message for key, message in errors.items() if key not in shown]
    return render(request, "core/site_settings.html", {"rows": rows, "launch": launch, "page_errors": page_errors})


# --- public pages: legal notices and the offline page -------------------------------------------

LEGAL_PAGES = {
    "member-agreement": ("Member agreement", [
        "This page will hold the agreement every member accepts on joining: membership by sponsorship, "
        "the annual fee and its automatic renewal, conduct, moderation and the Rap Sheet, and what "
        "happens to an account that lapses or is banned.",
    ]),
    "privacy": ("Privacy notice", [
        "This page will describe what the forum keeps about members and why: account and identity "
        "details, posts and direct messages, sessions and the shortened addresses they record, "
        "payments through Stripe, and the permanent-ban list, which survives erasure.",
    ]),
}


@require_GET
def legal(request, slug):
    """Rule 65: the legal pages exist, public, with placeholder text marked as a draft."""
    from django.http import Http404

    if slug not in LEGAL_PAGES:
        raise Http404
    title, body = LEGAL_PAGES[slug]
    return render(request, "legal/page.html", {"title": title, "body": body})


@require_GET
def offline(request):
    """Shown by the service worker when an installed copy has no connection. No member content."""
    return render(request, "offline.html")


@require_GET
def install_help(request):
    return render(request, "help/install.html")


def trace_watermark(request):
    """Rule 61: Admins and Owners paste a leaked excerpt to find the session, member and day it was
    served to. Each use is audited, with ids only."""
    from django.core.exceptions import PermissionDenied
    from django.db import transaction

    from accounts.models import UserSession
    from audit import log
    from core import watermark
    from core.permissions import can

    decision = can(request.user, "watermark.trace")
    if not decision:
        raise PermissionDenied(decision.reason)
    results = None
    if request.method == "POST":
        results = []
        for seed, day in watermark.find(request.POST.get("excerpt", "")):
            matches = [s for s in UserSession.objects.filter(watermark_seed=f"{seed:08x}").select_related("user")]
            results.append({"seed": f"{seed:08x}", "date": watermark.day_date(day), "sessions": matches})
        with transaction.atomic():
            log.record(request.user, "watermark.trace", request.user, {
                "marks": len(results), "sessions": [s.pk for r in results for s in r["sessions"]],
            })
    return render(request, "core/trace.html", {"results": results})
