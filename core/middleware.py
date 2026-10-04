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


class ContentSecurityPolicyMiddleware:
    """Rule 64: scripts and styles only from the site itself, no inline script or style, so a
    rendering bug cannot run injected script. Images may also come from the object store, where
    attachments are served by signed URL (settings.CSP_EXTRA_IMG_SRC)."""

    def __init__(self, get_response):
        self.get_response = get_response
        img = " ".join(["'self'", *settings.CSP_EXTRA_IMG_SRC])
        self.policy = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            f"img-src {img}; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.policy)
        return response


class SessionBindingMiddleware:
    """Rules 57 to 59: give every browser a device cookie, and sign a session out when it has been
    revoked (signed out elsewhere, a concurrent location, a factor reset) or has passed its lifetime
    or idle limit. Records when each session was last active."""

    TOUCH_EVERY = 60  # seconds between last_seen_at writes

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.utils import timezone

        cookie = request.COOKIES.get(settings.DEVICE_COOKIE_NAME, "")
        fresh = not cookie
        if fresh:
            from accounts.sessions import new_device_id

            cookie = new_device_id()
        request.device_id = cookie
        if request.user.is_authenticated:
            ended = self._check(request, timezone.now())
            if ended is not None:
                response = ended
            else:
                response = self.get_response(request)
        else:
            response = self.get_response(request)
        if fresh:
            response.set_cookie(settings.DEVICE_COOKIE_NAME, cookie, max_age=settings.DEVICE_COOKIE_AGE,
                                httponly=True, secure=settings.SESSION_COOKIE_SECURE, samesite="Lax")
        return response

    def _check(self, request, now):
        from django.contrib import messages
        from django.contrib.auth import logout

        from accounts import sessions
        from accounts.models import UserSession

        record = UserSession.objects.filter(session_key=request.session.session_key).select_related("user").first()
        if record is None:
            sessions.start(request, request.user, check=False)
            return None
        reason = sessions.ended_reason(record, now)
        if reason is None:
            if (now - record.last_seen_at).total_seconds() >= self.TOUCH_EVERY:
                UserSession.objects.filter(pk=record.pk).update(last_seen_at=now)
            return None
        if record.revoked_at is None:
            sessions.end(record, reason, now, delete_session=False)
        logout(request)
        message = sessions.ENDED_MESSAGES.get(reason)
        if message:
            messages.warning(request, message)
        if request.method == "GET" and "HX-Request" not in request.headers:
            return redirect_to_login(request.get_full_path())
        return HttpResponse("Signed out. Please sign in again.", status=401, content_type="text/plain")


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
