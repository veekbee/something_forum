from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.permissions import can


@require_GET
def robots_txt(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


@require_GET
def home(request):
    # Forum views arrive with build step 3; for now, signing in with TOTP lands here.
    return render(request, "core/home.html", {
        "may_invite": can(request.user, "member.sponsor"),
        "may_review": can(request.user, "invitation.review_queue"),
    })
