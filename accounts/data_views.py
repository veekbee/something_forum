"""Your data: export (rule 79), and the Owners' data-request page. Decisions are made by
core.permissions and accounts.data_rights."""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts import data_rights
from accounts.models import User
from core import registry
from core.models import DataRequest
from core.permissions import can


def _serve(item, expire_seconds):
    if settings.AWS_STORAGE_BUCKET_NAME:
        return HttpResponseRedirect(default_storage.url(item.file_key, expire=expire_seconds))
    response = FileResponse(default_storage.open(item.file_key, "rb"), as_attachment=True,
                            filename=f"forum-data-{item.completed_at:%Y-%m-%d}.zip", content_type="application/zip")
    response["Cache-Control"] = "private, no-store"
    return response


def your_data(request):
    user = request.user
    if request.method == "POST" and request.POST.get("export"):
        try:
            data_rights.request_export(user, getattr(request, "user_session", None))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, "Your export is being prepared. We'll let you know when it's ready.")
        return redirect("your_data")
    latest = DataRequest.objects.filter(user=user, kind=DataRequest.Kind.EXPORT, opened_by__isnull=True).order_by(
        "-requested_at").first()
    ready = data_rights.downloadable(user)
    return render(request, "account/data.html", {
        "latest": latest,
        "ready": ready,
        "kept_until": ready and ready.completed_at + timezone.timedelta(days=registry.site_value("export.keep_days")),
        "next_allowed": data_rights.next_export_allowed(user),
        "may_export": can(user, "data.export", user),
    })


@require_GET
def export_download(request, pk):
    item = get_object_or_404(DataRequest, pk=pk, user=request.user, kind=DataRequest.Kind.EXPORT,
                             opened_by__isnull=True)
    if item.status != DataRequest.Status.READY:
        raise Http404
    data_rights.record_download(item, request.user)
    return _serve(item, registry.site_value("export.link_hours") * 3600)


def export_link(request, token):
    """The emailed link for a removed member, who cannot sign in (rule 9). Opening it shows a
    button, so a mail scanner fetching the link does not use it up; the button downloads once."""
    if request.method == "POST":
        item = data_rights.use_token(token)
        if item is None:
            return render(request, "account/export_link.html", {"gone": True}, status=410)
        return _serve(item, 300)
    item = data_rights.by_token(token)
    return render(request, "account/export_link.html", {"gone": item is None}, status=200 if item else 410)


def data_requests(request):
    """The Owners' page: open an export for a former member (rule 79)."""
    decision = can(request.user, "data.requests")
    if not decision:
        raise PermissionDenied(decision.reason)
    if request.method == "POST" and request.POST.get("export_for"):
        who = request.POST["export_for"].strip().lstrip("@").lower()
        member = User.objects.filter(slug=who).first() or User.objects.filter(email__iexact=who).first()
        try:
            if member is None:
                raise ValidationError("No member with that profile address or email.")
            data_rights.open_export_for(request.user, member)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, f"An export for {member.display_name} is being prepared; "
                                      f"the link goes to the address on file.")
        return redirect("data_requests")
    exports = DataRequest.objects.filter(kind=DataRequest.Kind.EXPORT, opened_by__isnull=False).select_related(
        "user", "opened_by").order_by("-requested_at")[:50]
    return render(request, "account/data_requests.html", {"exports": exports})
