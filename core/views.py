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
