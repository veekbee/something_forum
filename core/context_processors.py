from core.permissions import can


def nav(request):
    """Navigation links, decided by the permission service rather than by role checks in templates."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.status == user.Status.INVITED:
        return {}
    from boards.messages import unread_count

    return {
        "nav_forum": bool(can(user, "search.use")),
        "nav_unread_messages": unread_count(user),
        "nav_invite": bool(can(user, "member.sponsor")),
        "nav_review": bool(can(user, "invitation.review_queue")),
    }
