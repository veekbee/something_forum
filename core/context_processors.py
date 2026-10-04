from core.permissions import can


def site(request):
    """The forum's name, from the site.name setting, and a supplied logo if configured, on every
    page including public ones (docs/DESIGN.md, Visual design)."""
    from django.conf import settings
    from django.templatetags.static import static

    from core import registry

    return {"site_name": registry.site_value("site.name"),
            "site_logo": static(settings.SITE_LOGO) if settings.SITE_LOGO else ""}


# url names that belong to each of the four member destinations, for the current-page marker
SECTIONS = {
    "forum": {"home", "subforum", "thread", "new_thread", "reply", "post_link", "edit_post", "search", "edit_title",
              "title_revisions", "post_revisions", "move_thread", "end_thread", "delete_post"},
    "messages": {"inbox", "conversation", "new_conversation"},
    "notifications": {"notifications", "notification_settings"},
}


def posting_band(user):
    """The one persistent band shown under the masthead when something stops the member posting
    (rule 75), most pressing first: discipline, then the request-rate flag, then a lapse, then a
    sponsorship transfer. A suspension limited to some sub-forums shows only on their pages
    (scoped_band)."""
    from django.urls import reverse

    from moderation.models import ModerationAction, Report
    from sponsorship.transfers import awaiting_sponsor

    def until(action):
        return f"until {action.ends_at:%-d %b %Y}" if action.ends_at else "until further notice"

    record = reverse("rap_sheet", args=[user.slug])
    active = ModerationAction.objects.in_force().sitewide().filter(target_user=user)
    probation = active.filter(kind=ModerationAction.Kind.PROBATION).order_by("-ends_at").first()
    if probation is not None:
        return {"text": f"You are on Probation {until(probation)}: you can read, but not post.",
                "link": record, "link_text": "Your record"}
    suspension = active.filter(kind=ModerationAction.Kind.SUSPENSION).order_by("-ends_at").first()
    if suspension is not None:
        return {"text": f"Your posting is suspended {until(suspension)}.", "link": record, "link_text": "Your record"}
    if Report.objects.waiting().filter(kind=Report.Kind.FLAG_REQUEST_RATE, user=user).exists():
        return {"text": "Your account is read-only while a Moderator reviews unusually heavy activity. "
                        "Nothing else happens until they do.", "link": "", "link_text": ""}
    if user.status == user.Status.READ_ONLY:
        return {"text": "Your membership has lapsed, so your account is read-only.",
                "link": reverse("billing"), "link_text": "Renew"}
    if awaiting_sponsor(user):
        return {"text": "You need a new sponsor, and your account is read-only until you have one.",
                "link": reverse("transfer_status"), "link_text": "What this means"}
    return None


def scoped_band(user, subforum):
    """For pages of one sub-forum: a suspension limited to it, if no site-wide band shows."""
    from moderation.models import ModerationAction

    if subforum is None:
        return None
    action = ModerationAction.objects.in_force().filter(
        target_user=user, kind=ModerationAction.Kind.SUSPENSION, scope_subforums=subforum
    ).order_by("-ends_at").first()
    if action is None:
        return None
    when = f"until {action.ends_at:%-d %b %Y}" if action.ends_at else "until further notice"
    from django.urls import reverse

    return {"text": f"Your posting in {subforum.name} is suspended {when}.",
            "link": reverse("rap_sheet", args=[user.slug]), "link_text": "Your record"}


def _section(request, user):
    match = getattr(request, "resolver_match", None)
    name = match.url_name if match else ""
    if name == "member_profile" and match.kwargs.get("slug") == user.slug:
        return "profile"
    return next((section for section, names in SECTIONS.items() if name in names), "")


def nav(request):
    """Navigation links, decided by the permission service rather than by role checks in templates."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.status == user.Status.INVITED:
        return {}
    from boards.messages import unread_count

    staff_queue = bool(can(user, "queue.view"))
    queue_count = 0
    if staff_queue:
        from moderation import queue

        queue_count = len(queue.items(user))
    return {
        "nav_section": _section(request, user),
        "nav_band": posting_band(user),
        "nav_queue_count": queue_count,
        "nav_forum": bool(can(user, "search.use")),
        "nav_unread_messages": unread_count(user),
        "nav_unread_notifications": user.notifications.filter(read_at__isnull=True).exclude(kind="dm").count(),
        "nav_invite": bool(can(user, "member.sponsor")),
        "nav_review": bool(can(user, "invitation.review_queue")),
        "nav_queue": bool(can(user, "queue.view")),
        "nav_audit": bool(can(user, "audit.view")),
        "nav_settings": bool(can(user, "site_setting.write")),
        "nav_trace": bool(can(user, "watermark.trace")),
    }
