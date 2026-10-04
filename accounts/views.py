from allauth.account.views import LogoutView as AllauthLogoutView


class LogoutView(AllauthLogoutView):
    """allauth's logout, plus the header that empties the browser's caches for this site and
    unregisters the service worker, which cannot see a logout itself (design rule 14)."""

    def post(self, *args, **kwargs):
        response = super().post(*args, **kwargs)
        response["Clear-Site-Data"] = '"cache", "storage"'
        return response


def sessions_page(request):
    """Rule 59: members see their own active sessions and can sign out any of them, or all but
    this one."""
    from django.shortcuts import get_object_or_404, redirect, render

    from accounts import sessions
    from accounts.models import UserSession

    current = request.session.session_key
    if request.method == "POST":
        if request.POST.get("all_others"):
            sessions.sign_out_others(request.user, current)
        elif request.POST.get("session", "").isdigit():
            record = get_object_or_404(UserSession, pk=int(request.POST["session"]), user=request.user)
            sessions.sign_out(request.user, record, current)
        return redirect("sessions")
    return render(request, "account/sessions.html", {"sessions": sessions.active_sessions(request.user),
                                                     "current": current})


def leave_page(request):
    """A member ends their own membership (rule 66)."""
    from django.contrib.auth import logout
    from django.core.exceptions import PermissionDenied
    from django.shortcuts import render

    from accounts import removal
    from core.permissions import can

    decision = can(request.user, "member.leave", request.user)
    if request.method == "POST" and request.POST.get("confirm") == "leave":
        if not decision:
            raise PermissionDenied(decision.reason)
        removal.leave(request.user, request.POST.get("reason", ""))
        logout(request)
        response = render(request, "account/left.html")
        response["Clear-Site-Data"] = '"cache", "storage"'
        return response
    return render(request, "account/leave.html", {"may_leave": decision})
