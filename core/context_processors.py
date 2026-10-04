from core.permissions import can


def site(request):
    """The forum's name, from the site.name setting, on every page including public ones."""
    from core import registry

    return {"site_name": registry.site_value("site.name")}


def nav(request):
    """Navigation links, decided by the permission service rather than by role checks in templates."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.status == user.Status.INVITED:
        return {}
    from boards.messages import unread_count
    from sponsorship.transfers import awaiting_sponsor

    return {
        "nav_forum": bool(can(user, "search.use")),
        "nav_unread_messages": unread_count(user),
        "nav_unread_notifications": user.notifications.filter(read_at__isnull=True).exclude(kind="dm").count(),
        "nav_invite": bool(can(user, "member.sponsor")),
        "nav_review": bool(can(user, "invitation.review_queue")),
        "nav_queue": bool(can(user, "queue.view")),
        "nav_audit": bool(can(user, "audit.view")),
        "nav_settings": bool(can(user, "site_setting.write")),
        "nav_transfer": awaiting_sponsor(user),
    }
