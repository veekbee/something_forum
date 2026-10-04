from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse
from django.shortcuts import redirect

from accounts.models import User
from core import registry


class NoIndexMiddleware:
    """Every response tells crawlers to stay out."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


def _matches(path, prefixes):
    return any(path.startswith(prefix) for prefix in prefixes)


class AccessControlMiddleware:
    """Deny by default (design rule 9). Without a session, only the public paths respond. With a
    session but no TOTP authenticator, only the enrolment pages respond. An invited account that
    is not yet approved reaches only its onboarding pages (design rule 15)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if _matches(path, settings.PUBLIC_PATH_PREFIXES):
            return self.get_response(request)

        if not request.user.is_authenticated:
            if request.method == "GET" and "HX-Request" not in request.headers:
                return redirect_to_login(request.get_full_path())
            return HttpResponse("Authentication required.", status=401, content_type="text/plain")

        from moderation.permanent import is_permanently_banned

        if is_permanently_banned(request.user):
            # A session from before the ban ends here (rule 46).
            from django.contrib.auth import logout
            from django.shortcuts import render

            logout(request)
            return render(request, "account/permanently_banned.html", status=403)

        if (
            registry.site_value("auth.require_totp")
            and not _matches(path, settings.TOTP_ENROLMENT_PATH_PREFIXES)
            and not Authenticator.objects.filter(user=request.user, type=Authenticator.Type.TOTP).exists()
        ):
            if request.method == "GET" and "HX-Request" not in request.headers:
                return redirect("mfa_activate_totp")
            return HttpResponse("Two-factor authentication required.", status=403, content_type="text/plain")

        if request.user.status == User.Status.INVITED and not _matches(
            path, settings.ONBOARDING_PATH_PREFIXES + settings.TOTP_ENROLMENT_PATH_PREFIXES
        ):
            if request.method == "GET" and "HX-Request" not in request.headers:
                return redirect("onboarding_status")
            return HttpResponse("Your invitation is awaiting review.", status=403, content_type="text/plain")

        return self.get_response(request)
