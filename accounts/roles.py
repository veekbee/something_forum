"""Role lookups. Only the permission service and services should call these; views ask can()."""

from accounts.models import Role, RoleAssignment

OWNER, ADMIN, MODERATOR, TENURED, FULL, PROVISIONAL, GUEST = (
    "owner", "admin", "moderator", "tenured", "full", "provisional", "guest",
)


def trust_role(user):
    """The highest unrevoked global (unscoped) role, or None."""
    if not getattr(user, "is_authenticated", False):
        return None
    assignment = (
        RoleAssignment.objects.filter(user=user, revoked_at__isnull=True, scope_subforum__isnull=True)
        .select_related("role")
        .order_by("-role__rank")
        .first()
    )
    return assignment.role if assignment else None


def rank_of(role_name):
    return Role.objects.only("rank").get(name=role_name).rank


def trust_rank(user):
    role = trust_role(user)
    return role.rank if role else 0


def is_admin_or_owner(user):
    role = trust_role(user)
    return role is not None and role.name in (ADMIN, OWNER)


def is_owner(user):
    role = trust_role(user)
    return role is not None and role.name == OWNER


def moderated_subforum_ids(user):
    """Sub-forums named in this user's scoped Moderator assignments, or None for a global
    Moderator (every sub-forum)."""
    rows = RoleAssignment.objects.filter(user=user, revoked_at__isnull=True, role__name=MODERATOR)
    if rows.filter(scope_subforum__isnull=True).exists():
        return None
    return set(rows.values_list("scope_subforum_id", flat=True))


def is_moderator(user):
    return RoleAssignment.objects.filter(user=user, revoked_at__isnull=True, role__name=MODERATOR).exists()


def moderates(user, subforum):
    """Admins and Owners moderate everywhere; Moderators globally or within their scope, which
    also covers sub-forums nested inside a scoped one."""
    if subforum is None:
        return is_admin_or_owner(user)
    if is_admin_or_owner(user):
        return True
    if not is_moderator(user):
        return False
    scoped = moderated_subforum_ids(user)
    if scoped is None:
        return True
    node = subforum
    while node is not None:
        if node.pk in scoped:
            return True
        node = node.parent
    return False


def leadership():
    """Members whose trust role is Admin or Owner."""
    from accounts.models import User

    return User.objects.filter(
        role_assignments__revoked_at__isnull=True,
        role_assignments__scope_subforum__isnull=True,
        role_assignments__role__name__in=(ADMIN, OWNER),
    ).distinct()


def owners():
    """Members whose trust role is Owner."""
    from accounts.models import User

    return User.objects.filter(
        role_assignments__revoked_at__isnull=True,
        role_assignments__scope_subforum__isnull=True,
        role_assignments__role__name=OWNER,
    ).distinct()
