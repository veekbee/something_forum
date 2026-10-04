"""Who may message whom (docs/DESIGN.md, Direct messages; rules 30 and 31).

A pair may message each other only if each side's rule allows the other:

- Full members and above, in good standing, message anyone at Full or above.
- Guests and Provisionals, and anyone read-only (lapsed payment or Probation) or suspended site-wide,
  message only their own sponsor and staff. Staff means Admins, Owners, and the Moderators of any
  sub-forum the member can read.
- A banned member messages only Admins and Owners.

Blocks are separate: a block stops new conversations, additions and mention notifications, and
stops a one-to-one conversation taking messages from the blocked side. Staff cannot be blocked.
"""

from accounts import roles
from accounts.models import Block, User

CLOSED, BANNED, LIMITED, NORMAL = "closed", "banned", "limited", "normal"


def _in_force(user, *kinds):
    from moderation.models import ModerationAction

    return ModerationAction.objects.in_force().filter(target_user=user, kind__in=kinds).exists()


def standing(user):
    if user.status in (User.Status.INVITED, User.Status.REMOVED, User.Status.TOMBSTONE):
        return CLOSED
    if user.status == User.Status.BANNED or _in_force(user, "ban"):
        return BANNED
    role = roles.trust_role(user)
    if role is None:
        return CLOSED
    if role.name in (roles.GUEST, roles.PROVISIONAL):
        return LIMITED
    if user.status in (User.Status.READ_ONLY, User.Status.SUSPENDED):
        return LIMITED
    if _in_force(user, "probation", "read_only", "suspension"):
        return LIMITED
    return NORMAL


def is_staff_role(user):
    return roles.is_admin_or_owner(user) or roles.is_moderator(user)


def is_staff_for(staff, member):
    """Admins and Owners, or a Moderator of any sub-forum `member` can read."""
    if roles.is_admin_or_owner(staff):
        return True
    if not roles.is_moderator(staff):
        return False
    from boards.visibility import readable_subforums

    return any(roles.moderates(staff, sf) for sf in readable_subforums(member))


def active_sponsor(user):
    from sponsorship.models import Sponsorship

    row = Sponsorship.objects.filter(member=user, ended_at__isnull=True).select_related("sponsor").first()
    return row.sponsor if row else None


def _allows(x, y, sx, sy):
    """Does x's side of the rule permit messaging y? sx, sy are their standings."""
    if sx == BANNED:
        return roles.is_admin_or_owner(y)
    if sx == LIMITED:
        sponsor = active_sponsor(x)
        return (sponsor is not None and sponsor.pk == y.pk) or is_staff_for(y, x)
    # x is a Full member or above in good standing.
    if sy == BANNED:
        return roles.is_admin_or_owner(x)
    if sy == LIMITED:
        sponsor = active_sponsor(y)
        return (sponsor is not None and sponsor.pk == x.pk) or is_staff_for(x, y)
    return roles.trust_rank(y) >= roles.rank_of(roles.FULL)


def may_message(a, b):
    if a.pk == b.pk:
        return False
    sa, sb = standing(a), standing(b)
    if CLOSED in (sa, sb):
        return False
    return _allows(a, b, sa, sb) and _allows(b, a, sb, sa)


def has_blocked(blocker, blocked):
    """A block counts unless the blocked member is staff, who cannot be blocked."""
    if is_staff_role(blocked):
        return False
    return Block.objects.filter(blocker=blocker, blocked=blocked).exists()


def blocked_either_way(a, b):
    return has_blocked(a, b) or has_blocked(b, a)
