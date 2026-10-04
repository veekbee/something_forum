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
    from sponsorship.transfers import awaiting_sponsor

    staff_queue = bool(can(user, "queue.view"))
    queue_count = 0
    if staff_queue:
        from moderation import queue

        queue_count = len(queue.items(user))
    return {
        "nav_section": _section(request, user),
        "nav_queue_count": queue_count,
        "nav_lapsed": user.status == user.Status.READ_ONLY,
        "nav_forum": bool(can(user, "search.use")),
        "nav_unread_messages": unread_count(user),
        "nav_unread_notifications": user.notifications.filter(read_at__isnull=True).exclude(kind="dm").count(),
        "nav_invite": bool(can(user, "member.sponsor")),
        "nav_review": bool(can(user, "invitation.review_queue")),
        "nav_queue": bool(can(user, "queue.view")),
        "nav_audit": bool(can(user, "audit.view")),
        "nav_settings": bool(can(user, "site_setting.write")),
        "nav_trace": bool(can(user, "watermark.trace")),
        "nav_transfer": awaiting_sponsor(user),
    }
