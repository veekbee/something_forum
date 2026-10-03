"""The permission service. Every authorisation question goes through can(actor, action, target);
an action with no rule here is denied (design rule 1). Views and services never check roles
themselves.

can() only reads. Anything that must be audited, such as a Moderator opening a DM under a
grant, is recorded by the service that acts on the Decision (see Decision.via).
"""

from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from accounts import roles
from accounts.models import User

READABLE_STATUSES = {User.Status.GUEST, User.Status.ACTIVE, User.Status.READ_ONLY, User.Status.SUSPENDED}
WRITABLE_STATUSES = {User.Status.GUEST, User.Status.ACTIVE}

# Kinds a Moderator may initiate and approve; everything else needs an Admin or Owner.
MODERATOR_KINDS = {"note", "warning", "hold", "suspension", "read_only", "ban"}
# Kinds whose approver must be an Admin or Owner even when a Moderator initiates.
ADMIN_APPROVAL_KINDS = {"ban"}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    # How access was granted, when it matters to the caller (e.g. "dm_grant" must be audited).
    via: str = ""
    detail: Any = field(default=None, compare=False)

    def __bool__(self):
        return self.allowed


def allow(via="", detail=None):
    return Decision(True, via=via, detail=detail)


def deny(reason):
    return Decision(False, reason=reason)


@dataclass(frozen=True)
class ActionRequest:
    """Target for moderation.initiate: what a staff member proposes to do to whom."""

    kind: str
    target_user: User
    related_post: Any = None


@dataclass(frozen=True)
class RoleChange:
    """Target for role.grant and role.revoke."""

    user: User
    role_name: str
    scope_subforum: Any = None


_RULES = {}


def rule(action):
    def register(fn):
        _RULES[action] = fn
        return fn

    return register


def can(actor, action, target=None):
    if actor is None or not getattr(actor, "is_authenticated", False):
        return deny("not signed in")
    handler = _RULES.get(action)
    if handler is None:
        return deny(f"no rule for {action!r}")
    return handler(actor, target)


# --- account state -------------------------------------------------------------------------


def _in_force(user, *kinds):
    from moderation.models import ModerationAction

    return ModerationAction.objects.in_force().filter(target_user=user, kind__in=kinds).exists()


def _can_read_anything(user):
    if user.status not in READABLE_STATUSES:
        return deny(f"account is {user.status}")
    if _in_force(user, "ban"):
        return deny("account is banned")
    return allow()


def _can_write_anything(user):
    readable = _can_read_anything(user)
    if not readable:
        return readable
    if user.status not in WRITABLE_STATUSES:
        return deny(f"account is {user.status}")
    if _in_force(user, "suspension", "read_only"):
        return deny("posting is suspended")
    return allow()


def _rank_in(user, subforum):
    """Trust rank, raised to Moderator rank where the member moderates this sub-forum: for
    posting permissions the Moderator role is per sub-forum."""
    rank = roles.trust_rank(user)
    if roles.is_moderator(user) and roles.moderates(user, subforum):
        rank = max(rank, roles.rank_of(roles.MODERATOR))
    return rank


def _meets(user, subforum, key):
    return _rank_in(user, subforum) >= roles.rank_of(subforum.setting(key))


def _effective_staff_rank(user):
    """Trust rank, raised to Moderator rank for anyone holding a Moderator assignment, so a
    Tenured member who moderates one sub-forum outranks the members they moderate."""
    rank = roles.trust_rank(user)
    if roles.is_moderator(user):
        rank = max(rank, roles.rank_of(roles.MODERATOR))
    return rank


# --- reading and posting -------------------------------------------------------------------


@rule("subforum.read")
def _subforum_read(actor, subforum):
    base = _can_read_anything(actor)
    if not base:
        return base
    if roles.is_admin_or_owner(actor):
        return allow()
    if subforum.parent is not None and not _subforum_read(actor, subforum.parent):
        return deny("cannot read the parent sub-forum")
    if not _meets(actor, subforum, "subforum.min_read_role"):
        return deny("role is below this sub-forum's minimum to read")
    return allow()


@rule("subforum.start_thread")
def _subforum_start_thread(actor, subforum):
    from boards import limits

    readable = _subforum_read(actor, subforum)
    if not readable:
        return readable
    writable = _can_write_anything(actor)
    if not writable:
        return writable
    if subforum.is_archived:
        return deny("sub-forum is archived")
    if not _meets(actor, subforum, "subforum.min_thread_role"):
        return deny("role is below this sub-forum's minimum to start a thread")
    if limits.thread_limit_reached(actor, subforum):
        return deny("thread rate limit reached")
    if limits.post_limit_reached(actor, subforum):
        return deny("post rate limit reached")
    return allow()


@rule("thread.read")
def _thread_read(actor, thread):
    if thread.kind == thread.Kind.DM:
        return _dm_read(actor, thread)
    return _subforum_read(actor, thread.subforum)


def _dm_read(actor, thread):
    from moderation.models import DMAccessGrant

    base = _can_read_anything(actor)
    if not base:
        return base
    if thread.participants.filter(user=actor).exists():
        return allow(via="participant")
    if roles.is_admin_or_owner(actor):
        return allow(via="admin")
    if roles.is_moderator(actor):
        now = timezone.now()
        participant_ids = thread.participants.values_list("user_id", flat=True)
        grant = (
            DMAccessGrant.objects.filter(
                moderator=actor, revoked_at__isnull=True, expires_at__gt=now, subject_users__in=participant_ids
            )
            .order_by("expires_at")
            .first()
        )
        if grant is not None:
            return allow(via="dm_grant", detail=grant)
    return deny("not a participant in this conversation")


@rule("thread.reply")
def _thread_reply(actor, thread):
    from boards import limits

    if thread.kind == thread.Kind.DM:
        writable = _can_write_anything(actor)
        if not writable:
            return writable
        if not thread.participants.filter(user=actor).exists():
            return deny("not a participant in this conversation")
        return allow()
    readable = _thread_read(actor, thread)
    if not readable:
        return readable
    writable = _can_write_anything(actor)
    if not writable:
        return writable
    subforum = thread.subforum
    if subforum.is_archived or thread.state == thread.State.ARCHIVED:
        return deny("thread is archived")
    if thread.state == thread.State.LOCKED and not roles.moderates(actor, subforum):
        return deny("thread is locked")
    if not _meets(actor, subforum, "subforum.min_reply_role"):
        return deny("role is below this sub-forum's minimum to reply")
    if limits.post_limit_reached(actor, subforum):
        return deny("post rate limit reached")
    return allow()


@rule("post.read")
def _post_read(actor, post):
    readable = _thread_read(actor, post.thread)
    if not readable:
        return readable
    if post.deleted_at is not None and not roles.is_admin_or_owner(actor):
        return deny("post was deleted")
    if post.rejected_at is not None and not roles.moderates(actor, post.thread.subforum):
        return deny("post was rejected")
    if post.is_held and post.author_id != actor.pk and not roles.moderates(actor, post.thread.subforum):
        return deny("post is awaiting review")
    return readable


@rule("post.moderate")
def _post_moderate(actor, post):
    """Release or reject a held post."""
    base = _can_read_anything(actor)
    if not base:
        return base
    if not roles.moderates(actor, post.thread.subforum):
        return deny("not a moderator of this sub-forum")
    return allow()


# --- sponsorship ---------------------------------------------------------------------------


@rule("member.sponsor")
def _member_sponsor(actor, _target):
    """May this member invite anyone at all. The cap does not block inviting: an invitation
    beyond it is waitlisted (sponsorship.capacity)."""
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    role = roles.trust_role(actor)
    if role is None or role.rank < roles.rank_of(roles.FULL):
        return deny("only Full members and above may sponsor")
    if roles.is_admin_or_owner(actor):
        return allow()
    if _in_force(actor, "ban"):
        return deny("account is banned")
    if _in_force(actor, "sponsoring_suspension"):
        return deny("sponsoring privileges are suspended")
    return allow()


@rule("invitation.approve")
def _invitation_approve(actor, invitation):
    """Admin manual review. Approving a waitlisted invitation is how leadership goes over a
    sponsor's cap."""
    if not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners approve invitations")
    if invitation.status not in (invitation.Status.PENDING, invitation.Status.WAITLISTED):
        return deny(f"invitation is {invitation.status}")
    return allow()


@rule("sponsor_review.decide")
def _sponsor_review_decide(actor, review):
    if not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners decide sponsor reviews")
    if review.status != review.Status.PENDING:
        return deny("review is already decided")
    if review.sponsor_id == actor.pk:
        return deny("cannot decide a review of yourself")
    return allow()


# --- moderation ----------------------------------------------------------------------------


def _staff_may_act_on(actor, target_user):
    if target_user.pk == actor.pk:
        return deny("cannot act on yourself")
    if roles.trust_rank(target_user) >= _effective_staff_rank(actor) or (
        roles.is_moderator(target_user) and not roles.is_admin_or_owner(actor)
    ):
        return deny("target's role is not below yours")
    return allow()


def _staff_scope_ok(actor, related_post):
    """Moderators act within their sub-forums: a scoped Moderator needs a related post in scope."""
    if roles.is_admin_or_owner(actor):
        return True
    if not roles.is_moderator(actor):
        return False
    if related_post is not None:
        return roles.moderates(actor, related_post.thread.subforum)
    return roles.moderated_subforum_ids(actor) is None


@rule("moderation.initiate")
def _moderation_initiate(actor, request):
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    if not roles.is_admin_or_owner(actor):
        if request.kind not in MODERATOR_KINDS:
            return deny("only Admins and Owners may take this action")
        if not _staff_scope_ok(actor, request.related_post):
            return deny("outside your moderation scope")
    return _staff_may_act_on(actor, request.target_user)


@rule("moderation.approve")
def _moderation_approve(actor, action):
    if action.status != action.Status.PENDING:
        return deny("action is not awaiting approval")
    if action.initiated_by_id == actor.pk:
        return deny("approver must differ from initiator")
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    admin = roles.is_admin_or_owner(actor)
    if (action.kind in ADMIN_APPROVAL_KINDS or action.kind not in MODERATOR_KINDS) and not admin:
        return deny("this action needs an Admin or Owner to approve")
    if not admin and not _staff_scope_ok(actor, action.related_post):
        return deny("outside your moderation scope")
    return _staff_may_act_on(actor, action.target_user)


@rule("dm.grant_access")
def _dm_grant_access(actor, moderator):
    if not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners grant DM access")
    if not roles.is_moderator(moderator):
        return deny("DM access is granted to Moderators")
    return allow()


# --- roles and promotion -------------------------------------------------------------------


@rule("role.grant")
def _role_grant(actor, change):
    if change.user.pk == actor.pk:
        return deny("cannot change your own role")
    if change.role_name in (roles.OWNER, roles.ADMIN):
        return allow() if roles.is_owner(actor) else deny("only an Owner appoints Owners and Admins")
    if change.scope_subforum is not None and change.role_name != roles.MODERATOR:
        return deny("only the Moderator role takes a sub-forum scope")
    if roles.is_admin_or_owner(change.user) and not roles.is_owner(actor):
        return deny("only an Owner changes an Owner's or Admin's roles")
    if change.role_name == roles.MODERATOR:
        if not roles.is_admin_or_owner(actor):
            return deny("only Admins and Owners appoint Moderators")
        if roles.trust_rank(change.user) < roles.rank_of(roles.TENURED):
            return deny("Moderators are appointed from Tenured members")
        return allow()
    if change.role_name == roles.TENURED and roles.is_moderator(actor):
        return allow()
    if roles.is_admin_or_owner(actor):
        return allow()
    return deny("not allowed to change roles")


@rule("role.revoke")
def _role_revoke(actor, change):
    return _role_grant(actor, change)


@rule("promotion.recommend")
def _promotion_recommend(actor, member):
    from sponsorship.eligibility import full_promotion_eligible

    if member.pk == actor.pk:
        return deny("cannot recommend yourself")
    if actor.status != User.Status.ACTIVE or roles.trust_rank(actor) < roles.rank_of(roles.FULL):
        return deny("only Full members and above recommend")
    if not full_promotion_eligible(member):
        return deny("member is not yet eligible")
    return allow()


@rule("promotion.review")
def _promotion_review(actor, promotion):
    if promotion.status != promotion.Status.RECOMMENDED:
        return deny("promotion is not awaiting review")
    if not (roles.is_moderator(actor) or roles.is_admin_or_owner(actor)):
        return deny("only Moderators review promotions")
    return allow()


@rule("promotion.decide")
def _promotion_decide(actor, promotion):
    if promotion.status != promotion.Status.REVIEWED:
        return deny("promotion has not been reviewed")
    if not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners decide promotions")
    return allow()


# --- Owner and Admin only ------------------------------------------------------------------


@rule("identity.read")
def _identity_read(actor, _user):
    return allow() if roles.is_admin_or_owner(actor) else deny("identity data is for Admins only")


@rule("admin_site.view")
def _admin_site_view(actor, _target):
    return allow() if roles.is_admin_or_owner(actor) else deny("staff admin is for Admins only")


@rule("site_setting.write")
def _site_setting_write(actor, _key):
    return allow() if roles.is_owner(actor) else deny("only Owners change site settings")


@rule("data.destroy")
def _data_destroy(actor, _target):
    return allow() if roles.is_owner(actor) else deny("only Owners run data-destroying operations")
