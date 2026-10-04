"""The permission service. Every authorisation question goes through can(actor, action, target);
an action with no rule here is denied (design rule 1). Views and services never check roles
themselves.

can() only reads. Anything that must be audited, such as a Moderator opening a DM under a
grant, is recorded by the service that acts on the Decision (see Decision.via).
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.utils import timezone

from accounts import roles
from accounts.models import User
from core import registry

READABLE_STATUSES = {User.Status.GUEST, User.Status.ACTIVE, User.Status.READ_ONLY, User.Status.SUSPENDED}
WRITABLE_STATUSES = {User.Status.GUEST, User.Status.ACTIVE}

# Kinds a Moderator may initiate and approve; everything else needs an Admin or Owner.
MODERATOR_KINDS = {"note", "warning", "hold", "suspension", "probation", "ban"}
# Kinds that always need an Admin or Owner as initiator or approver (rule 36).
ADMIN_APPROVAL_KINDS = {"ban", "probation"}
# Kinds a Moderator may limit to one sub-forum they moderate.
SCOPABLE_KINDS = {"hold", "suspension"}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    # How access was granted, when it matters to the caller (e.g. "dm_grant" must be audited).
    via: str = ""
    detail: Any = field(default=None, compare=False)
    # A short machine-readable reason, where callers need to tell refusals apart.
    code: str = ""

    def __bool__(self):
        return self.allowed


def allow(via="", detail=None):
    return Decision(True, via=via, detail=detail)


def deny(reason, code=""):
    return Decision(False, reason=reason, code=code)


@dataclass(frozen=True)
class ActionRequest:
    """Target for moderation.initiate: what a staff member proposes to do to whom."""

    kind: str
    target_user: User
    related_post: Any = None
    scope_subforums: tuple = ()


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


def _in_force(user, *kinds, subforum=None):
    """Kinds in force on `user`: site-wide ones, plus, given a sub-forum, those limited to it."""
    from moderation.models import ModerationAction

    actions = ModerationAction.objects.in_force().filter(target_user=user, kind__in=kinds)
    actions = actions.applying_in(subforum) if subforum is not None else actions.sitewide()
    return actions.exists()


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
    if _in_force(user, "suspension", "probation"):
        return deny("posting is suspended")
    if _request_rate_flagged(user):
        return deny("account is read-only until a Moderator reviews unusual activity")
    return allow()


def _request_rate_flagged(user):
    from moderation.models import Report

    return Report.objects.waiting().filter(kind=Report.Kind.FLAG_REQUEST_RATE, user=user).exists()


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
    if subforum.is_archived or subforum.is_ending_area:
        return deny("sub-forum is read-only")
    if not _meets(actor, subforum, "subforum.min_thread_role"):
        return deny("role is below this sub-forum's minimum to start a thread")
    if _in_force(actor, "suspension", subforum=subforum):
        return deny("posting is suspended in this sub-forum")
    if limits.thread_limit_reached(actor, subforum):
        return deny("thread rate limit reached", code="rate_limit")
    if limits.post_limit_reached(actor, subforum):
        return deny("post rate limit reached", code="rate_limit")
    return allow()


@rule("thread.read")
def _thread_read(actor, thread):
    if thread.kind == thread.Kind.DM:
        return _dm_read(actor, thread)
    return _subforum_read(actor, thread.subforum)


CLOSED_STATUSES = {User.Status.INVITED, User.Status.REMOVED, User.Status.TOMBSTONE}


def _dm_read(actor, thread):
    """Participants, including those who left (they read up to when they left) and banned members,
    who keep their conversations. Admins and Owners; Moderators under a grant. Reads by anyone but a
    participant are audited by boards.services.open_thread (design rule 8)."""
    from moderation.models import DMAccessGrant

    if actor.status in CLOSED_STATUSES:
        return deny(f"account is {actor.status}")
    periods = list(thread.participants.filter(user=actor))
    if periods:
        return allow(via="participant", detail=periods)
    base = _can_read_anything(actor)
    if not base:
        return base
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
        return _dm_send(actor, thread)
    readable = _thread_read(actor, thread)
    if not readable:
        return readable
    writable = _can_write_anything(actor)
    if not writable:
        return writable
    subforum = thread.subforum
    if _is_ended(thread):
        return deny("thread is archived")
    if thread.state == thread.State.LOCKED and not roles.moderates(actor, subforum):
        return deny("thread is locked")
    if not _meets(actor, subforum, "subforum.min_reply_role"):
        return deny("role is below this sub-forum's minimum to reply")
    if _in_force(actor, "suspension", subforum=subforum):
        return deny("posting is suspended in this sub-forum")
    if limits.post_limit_reached(actor, subforum):
        return deny("post rate limit reached", code="rate_limit")
    return allow()


def _dm_send(actor, thread):
    """Rule 30: a participant sends only while they may still message every other participant; a
    one-to-one conversation stops taking messages from the side the other has blocked."""
    from boards import dm

    me = thread.participants.filter(user=actor, left_at__isnull=True).first()
    if me is None:
        return deny("not in this conversation")
    others = [p.user for p in thread.participants.filter(left_at__isnull=True).exclude(user=actor).select_related("user")]
    if not others:
        return deny("nobody else is in this conversation")
    for other in others:
        if not dm.may_message(actor, other):
            return deny(f"you can no longer message {other.display_name}")
    if len(others) == 1 and dm.has_blocked(others[0], actor):
        return deny("this conversation is not taking messages from you")
    return allow()


@rule("post.read")
def _post_read(actor, post):
    readable = _thread_read(actor, post.thread)
    if not readable:
        return readable
    if post.thread.kind == post.thread.Kind.DM and readable.via == "participant":
        if not any(
            p.joined_at <= post.created_at and (p.left_at is None or post.created_at <= p.left_at)
            for p in readable.detail
        ):
            return deny("sent while you were not in the conversation")
    if post.deleted_at is not None and not roles.is_admin_or_owner(actor):
        return deny("post was deleted")
    if post.rejected_at is not None and not roles.moderates(actor, post.thread.subforum):
        return deny("post was rejected")
    if post.is_held and post.author_id != actor.pk and not roles.moderates(actor, post.thread.subforum):
        return deny("post is awaiting review")
    return readable


@rule("post.read_in_history")
def _post_read_in_history(actor, post):
    """A member's own post history on their profile, which also shows their held and rejected
    posts. Anyone else sees what post.read allows."""
    base = _can_read_anything(actor)
    if not base:
        return base
    if post.author_id == actor.pk and post.deleted_at is None:
        return allow()
    return _post_read(actor, post)


def _in_graveyard(thread):
    return thread.subforum is not None and thread.subforum.kind == thread.subforum.Kind.GRAVEYARD


def _is_ended(thread):
    """Archived in place, or in the Graveyard or the Classics: no changes (design rule 13)."""
    subforum = thread.subforum
    return subforum.is_archived or subforum.is_ending_area or thread.state == thread.State.ARCHIVED


def _redactor(actor):
    """Admins and Owners may redact Graveyard threads, the one change allowed there (rule 26)."""
    return actor.status == User.Status.ACTIVE and roles.is_admin_or_owner(actor)


def _within_edit_window(post):
    window = post.thread.subforum.setting("subforum.edit_window_minutes")
    return window is None or timezone.now() - post.created_at <= timedelta(minutes=window)


def _dm_change(actor, post):
    """DM messages: their author, still in the conversation, within dm.edit_window_minutes."""
    if post.deleted_at is not None:
        return deny("message was deleted")
    if post.author_id != actor.pk:
        return deny("not your message")
    if not post.thread.participants.filter(user=actor, left_at__isnull=True).exists():
        return deny("not in this conversation")
    window = registry.site_value("dm.edit_window_minutes")
    if window is not None and timezone.now() - post.created_at > timedelta(minutes=window):
        return deny("edit window has closed")
    return allow(via="author")


def _post_change(actor, post, allow_redaction):
    thread = post.thread
    if thread.kind == thread.Kind.DM:
        return _dm_change(actor, post)
    if post.deleted_at is not None:
        return deny("post was deleted")
    if post.rejected_at is not None:
        return deny("post was rejected")
    if _in_graveyard(thread):
        if allow_redaction and _redactor(actor):
            return allow(via="redaction")
        return deny("thread is in the Graveyard")
    if _is_ended(thread):
        return deny("thread is archived")
    readable = _post_read(actor, post)
    if not readable:
        return readable
    writable = _can_write_anything(actor)
    if not writable:
        return writable
    if roles.moderates(actor, thread.subforum):
        return allow(via="staff")
    if post.author_id != actor.pk:
        return deny("not your post")
    if thread.state == thread.State.LOCKED:
        return deny("thread is locked")
    if not _within_edit_window(post):
        return deny("edit window has closed")
    return allow(via="author")


@rule("post.edit")
def _post_edit(actor, post):
    """Rule 25: staff edit any post in sub-forums they moderate; authors their own within the edit
    window and never in a locked thread. Admins and Owners may also redact in the Graveyard."""
    return _post_change(actor, post, allow_redaction=True)


@rule("post.delete")
def _post_delete(actor, post):
    """Soft delete, on the same terms as editing; nothing in the Graveyard is deleted."""
    return _post_change(actor, post, allow_redaction=False)


@rule("post.read_revisions")
def _post_read_revisions(actor, post):
    readable = _post_read(actor, post)
    if not readable:
        return readable
    if not roles.moderates(actor, post.thread.subforum):
        return deny("edit history is visible to staff")
    return allow()


@rule("post.purge_revisions")
def _post_purge_revisions(actor, post):
    if not (actor.status == User.Status.ACTIVE and roles.is_owner(actor)):
        return deny("only Owners purge revisions")
    if not _in_graveyard(post.thread):
        return deny("revisions are purged only for Graveyard threads")
    return allow()


@rule("thread.edit_title")
def _thread_edit_title(actor, thread):
    """The starter within the edit window of their first post; staff at any time."""
    if thread.kind == thread.Kind.DM:
        return deny("direct messages have no title to edit")
    if _in_graveyard(thread):
        return allow(via="redaction") if _redactor(actor) else deny("thread is in the Graveyard")
    if _is_ended(thread):
        return deny("thread is archived")
    readable = _thread_read(actor, thread)
    if not readable:
        return readable
    writable = _can_write_anything(actor)
    if not writable:
        return writable
    if roles.moderates(actor, thread.subforum):
        return allow(via="staff")
    if thread.author_id != actor.pk:
        return deny("only the thread's starter may edit its title")
    if thread.state == thread.State.LOCKED:
        return deny("thread is locked")
    first = thread.posts.filter(author=actor).order_by("created_at", "pk").first()
    if first is None or not _within_edit_window(first):
        return deny("edit window has closed")
    return allow(via="author")


@rule("thread.read_title_revisions")
def _thread_read_title_revisions(actor, thread):
    readable = _thread_read(actor, thread)
    if not readable:
        return readable
    if thread.subforum is None or not roles.moderates(actor, thread.subforum):
        return deny("title history is visible to staff")
    return allow()


def _thread_state_change(actor, thread, staff_check, who):
    if thread.kind == thread.Kind.DM:
        return deny("direct messages have no thread state")
    if _is_ended(thread):
        return deny("thread is archived")
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    if not staff_check(actor, thread.subforum):
        return deny(f"only {who} may do this")
    return allow()


def _leadership(actor, _subforum):
    return roles.is_admin_or_owner(actor)


@rule("thread.lock")
def _thread_lock(actor, thread):
    """Lock or unlock. Moderators may lock within their sub-forums (rule 27)."""
    return _thread_state_change(actor, thread, roles.moderates, "staff of this sub-forum")


@rule("thread.pin")
def _thread_pin(actor, thread):
    """Pin or unpin: Admins and Owners (rule 27)."""
    return _thread_state_change(actor, thread, _leadership, "Admins and Owners")


@rule("thread.archive")
def _thread_archive(actor, thread):
    """Archive in place: Admins and Owners (rule 26)."""
    return _thread_state_change(actor, thread, _leadership, "Admins and Owners")


@rule("thread.graveyard")
def _thread_graveyard(actor, thread):
    """What deleting a thread means: Moderators in their sub-forums, Admins, Owners (rule 26)."""
    return _thread_state_change(actor, thread, roles.moderates, "staff of this sub-forum")


@rule("thread.classics")
def _thread_classics(actor, thread):
    return _thread_state_change(actor, thread, _leadership, "Admins and Owners")


@dataclass(frozen=True)
class MoveRequest:
    thread: Any
    destination: Any


@rule("thread.move")
def _thread_move(actor, request):
    """Moderators move threads only between sub-forums they moderate; Admins and Owners anywhere
    (rule 27). Ending areas are reached by the ending actions, not by moving."""
    thread, destination = request.thread, request.destination
    if destination.is_ending_area or destination.is_archived:
        return deny("threads cannot be moved there")
    if destination.pk == thread.subforum_id:
        return deny("the thread is already there")

    def both(a, _subforum):
        return roles.moderates(a, thread.subforum) and roles.moderates(a, destination)

    return _thread_state_change(actor, thread, both, "staff of both sub-forums")


@rule("thread.restore")
def _thread_restore(actor, thread):
    """Unarchive, or bring back from the Graveyard or the Classics: Owners only (rule 26)."""
    if thread.kind == thread.Kind.DM or not _is_ended(thread) or thread.subforum.is_archived:
        return deny("thread is not archived")
    if actor.status != User.Status.ACTIVE or not roles.is_owner(actor):
        return deny("only Owners restore threads")
    return allow()


# --- direct messages -------------------------------------------------------------------------


@dataclass(frozen=True)
class Conversation:
    """Target for dm.start: the people to message, without the starter."""

    others: tuple


@dataclass(frozen=True)
class Membership:
    """Target for dm.add and dm.remove."""

    thread: Any
    user: Any


def _group_ok(members):
    from boards import dm

    if len(members) > registry.site_value("dm.max_participants"):
        return deny(f"a conversation holds at most {registry.site_value('dm.max_participants')} people")
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            if not dm.may_message(a, b):
                return deny(f"{a.display_name} and {b.display_name} cannot message each other")
            if dm.blocked_either_way(a, b):
                return deny("someone in this conversation has blocked another")
    return allow()


@rule("dm.start")
def _dm_start(actor, conversation):
    from boards.models import Thread

    others = [u for u in conversation.others if u.pk != actor.pk]
    if not others:
        return deny("choose someone to message")
    since = timezone.now() - timedelta(hours=24)
    started = Thread.objects.filter(kind=Thread.Kind.DM, author=actor, created_at__gt=since).count()
    if started >= registry.site_value("dm.max_new_conversations_per_day"):
        return deny("you have started as many conversations as allowed today")
    return _group_ok([actor, *others])


@rule("dm.add")
def _dm_add(actor, membership):
    thread, newcomer = membership.thread, membership.user
    if thread.kind != thread.Kind.DM:
        return deny("not a conversation")
    if not thread.participants.filter(user=actor, left_at__isnull=True).exists():
        return deny("not in this conversation")
    current = [p.user for p in thread.participants.filter(left_at__isnull=True).select_related("user")]
    if any(u.pk == newcomer.pk for u in current):
        return deny(f"{newcomer.display_name} is already here")
    return _group_ok([*current, newcomer])


@rule("dm.leave")
def _dm_leave(actor, thread):
    if thread.kind != thread.Kind.DM or not thread.participants.filter(user=actor, left_at__isnull=True).exists():
        return deny("not in this conversation")
    return allow()


@rule("dm.remove")
def _dm_remove(actor, membership):
    """Members cannot remove each other; Admins and Owners can."""
    if actor.status != User.Status.ACTIVE or not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners remove people from a conversation")
    if not membership.thread.participants.filter(user=membership.user, left_at__isnull=True).exists():
        return deny("not in this conversation")
    return allow()


@rule("member.block")
def _member_block(actor, target):
    from boards import dm

    if actor.status in CLOSED_STATUSES or target.pk == actor.pk:
        return deny("cannot block")
    if dm.is_staff_role(target):
        return deny("staff cannot be blocked")
    if target.status in CLOSED_STATUSES:
        return deny("no such member")
    return allow()


# --- reports and the moderation queue --------------------------------------------------------


def _staff_active(actor):
    return actor.status == User.Status.ACTIVE and (roles.is_moderator(actor) or roles.is_admin_or_owner(actor))


def _global_moderator(actor):
    return roles.is_moderator(actor) and roles.moderated_subforum_ids(actor) is None


def _report_in_scope(actor, report):
    """The moderation queue table: sub-forum items to that sub-forum's Moderators, member items to
    global Moderators, DM reports to Admins and Owners; Admins and Owners see everything."""
    from boards.models import SubForum
    from moderation.models import Report

    if roles.is_admin_or_owner(actor):
        return True
    if report.kind == Report.Kind.DM:
        return False
    if report.kind == Report.Kind.FLAG_REQUEST_RATE:
        return roles.is_moderator(actor)
    if report.related_promotion_id is not None:
        return roles.is_moderator(actor)
    if report.related_action_id is not None:
        action = report.related_action
        scopes = list(action.scope_subforums.all())
        if action.related_post is not None and not scopes:
            scopes = [action.related_post.thread.subforum]
        if scopes:
            return any(sf is not None and roles.moderates(actor, sf) for sf in scopes)
        return _global_moderator(actor)
    subforum = None
    if report.post_id is not None:
        subforum = report.post.thread.subforum
        if subforum is None:
            return False
    elif report.details.get("subforum"):
        subforum = SubForum.objects.filter(pk=report.details["subforum"]).first()
    if subforum is not None:
        return roles.moderates(actor, subforum)
    return _global_moderator(actor)


@rule("queue.view")
def _queue_view(actor, _target):
    return allow() if _staff_active(actor) else deny("the moderation queue is for staff")


@rule("report.view")
def _report_view(actor, report):
    if not _staff_active(actor) or not _report_in_scope(actor, report):
        return deny("not in your moderation scope")
    return allow()


@rule("report.resolve")
def _report_resolve(actor, report):
    """Rule 34: only Admins and Owners resolve escalated items and DM reports; a handled item stays
    handled."""
    viewable = _report_view(actor, report)
    if not viewable:
        return viewable
    if report.status == report.Status.RESOLVED:
        return deny("already handled", code="handled")
    if report.status == report.Status.ESCALATED and not roles.is_admin_or_owner(actor):
        return deny("escalated items are resolved by Admins and Owners")
    return allow()


@rule("queue.escalate")
def _queue_escalate(actor, item):
    """Rule 34: any queue item a Moderator can see can be escalated. Reports and flags are marked
    escalated themselves; a held post, pending action or promotion gets an escalation report."""
    from boards.models import Post
    from moderation.models import ModerationAction
    from sponsorship.models import Promotion

    if not _staff_active(actor) or roles.is_admin_or_owner(actor):
        return deny("Admins and Owners resolve items directly")
    if isinstance(item, Post):
        if _escalated(post=item):
            return deny("already escalated", code="handled")
        return _post_moderate(actor, item) if item.is_held else deny("not waiting in the queue")
    if isinstance(item, ModerationAction):
        if _escalated(related_action=item):
            return deny("already escalated", code="handled")
        return _eligible_approver(actor, item)
    if isinstance(item, Promotion):
        if _escalated(related_promotion=item):
            return deny("already escalated", code="handled")
        return _promotion_review(actor, item)
    return deny("this item cannot be escalated")


@rule("report.escalate")
def _report_escalate(actor, report):
    viewable = _report_view(actor, report)
    if not viewable:
        return viewable
    if report.status != report.Status.OPEN:
        return deny("already handled" if report.status == report.Status.RESOLVED else "already escalated",
                    code="handled")
    if roles.is_admin_or_owner(actor):
        return deny("Admins and Owners resolve items directly")
    return allow()


@rule("report.note")
def _report_note(actor, report):
    viewable = _report_view(actor, report)
    if not viewable:
        return viewable
    if report.status == report.Status.RESOLVED:
        return deny("already handled", code="handled")
    return allow()


@rule("report.create")
def _report_create(actor, target):
    """Every member, Guests included, reports posts they can read, DM messages in conversations they
    take part in, and members; at most reports.max_per_member_per_day (rule 33)."""
    from boards.models import Post
    from moderation.models import Report

    base = _can_read_anything(actor)
    if not base:
        return base
    since = timezone.now() - timedelta(hours=24)
    if Report.objects.filter(reporter=actor, created_at__gt=since).count() >= registry.site_value(
        "reports.max_per_member_per_day"
    ):
        return deny("you have made as many reports as allowed today")
    if isinstance(target, Post):
        if target.author_id == actor.pk:
            return deny("cannot report your own post")
        readable = _post_read(actor, target)
        if not readable:
            return readable
        if target.thread.kind == target.thread.Kind.DM and readable.via != "participant":
            return deny("only people in a conversation report its messages")
        return allow()
    if target.pk == actor.pk:
        return deny("cannot report yourself")
    return _member_view_profile(actor, target)


# --- staff views ---------------------------------------------------------------------------


@rule("member.staff_view")
def _member_staff_view(actor, member):
    """The per-member view (rule 37). Moderators get the limited tier; see member.staff_view_full."""
    if not _staff_active(actor):
        return deny("the per-member view is for staff")
    if member.status in CLOSED_STATUSES - {User.Status.REMOVED}:
        return deny("no such member")
    return allow(via="full" if roles.is_admin_or_owner(actor) else "limited")


@rule("member.staff_view_full")
def _member_staff_view_full(actor, member):
    """DMs, payment, sessions, blocks and the identity control: Admins and Owners only."""
    base = _member_staff_view(actor, member)
    if not base:
        return base
    return allow() if base.via == "full" else deny("Admins and Owners only")


@rule("audit.view")
def _audit_view(actor, _target):
    if actor.status != User.Status.ACTIVE or not roles.is_admin_or_owner(actor):
        return deny("the audit log is for Admins and Owners")
    return allow()


@rule("feed.view")
def _feed_view(actor, _target):
    return allow() if _staff_active(actor) else deny("the feed is for staff")


# --- billing -------------------------------------------------------------------------------


def _banned(user):
    return user.status == User.Status.BANNED or _in_force(user, "ban", "permanent_ban")


@rule("billing.view")
def _billing_view(actor, member):
    """The billing page is the member's own (rule 50); a banned member reaches only the ban
    payment page instead."""
    if actor.pk != member.pk:
        return deny("billing pages are private")
    if actor.status in CLOSED_STATUSES or _banned(actor):
        return deny("not available")
    if roles.trust_role(actor) is None:
        return deny("not a member")
    return allow()


@rule("billing.pay_membership")
def _billing_pay_membership(actor, _target):
    """A Guest may pay at any time after approval (rule 41); a lapsed member renews. Someone already
    paid up, renewing automatically, or comped has nothing to pay."""
    from billing.models import Subscription

    base = _billing_view(actor, actor)
    if not base:
        return base
    sub = Subscription.objects.filter(user=actor).first()
    if sub is not None:
        if sub.status == Subscription.Status.COMPED:
            return deny("your membership is complimentary")
        if sub.stripe_subscription_id and not sub.cancel_at_period_end:
            return deny("your membership renews automatically; manage it in the billing portal")
    return allow()


@rule("billing.portal")
def _billing_portal(actor, _target):
    from billing.models import Subscription

    base = _billing_view(actor, actor)
    if not base:
        return base
    if not Subscription.objects.filter(user=actor).exclude(stripe_customer_id="").exists():
        return deny("no payment account yet")
    return allow()


# --- members ---------------------------------------------------------------------------------

HIDDEN_STATUSES = {User.Status.INVITED, User.Status.REMOVED, User.Status.TOMBSTONE}


@rule("member.view_profile")
def _member_view_profile(actor, member):
    base = _can_read_anything(actor)
    if not base:
        return base
    if member.status in HIDDEN_STATUSES:
        return deny("no such member")
    return allow()


@rule("member.view_record")
def _member_view_record(actor, member):
    """The public disciplinary record is for members at Provisional and above."""
    viewable = _member_view_profile(actor, member)
    if not viewable:
        return viewable
    if roles.trust_rank(actor) < roles.rank_of(roles.PROVISIONAL):
        return deny("the disciplinary record is for Provisional members and above")
    return allow()


@rule("member.private_stats")
def _member_private_stats(actor, member):
    """A member's own post count and promotion progress, shown to nobody else (rule 28)."""
    return allow() if actor.pk == member.pk else deny("only the member sees this")


@rule("thread.follow")
def _thread_follow(actor, thread):
    """Follow or unfollow a forum thread, for "replies in threads you follow" notifications."""
    if thread.kind != thread.Kind.DISCUSSION:
        return deny("conversations notify their participants already")
    return _thread_read(actor, thread)


@rule("search.use")
def _search_use(actor, _target):
    return _can_read_anything(actor)


def _escalated(**item):
    """An open escalation points at this queue item, so it waits for an Admin or Owner."""
    from moderation.models import Report

    return Report.objects.waiting().filter(kind=Report.Kind.ESCALATION, **item).exists()


@rule("post.moderate")
def _post_moderate(actor, post):
    """Release or reject a held post."""
    base = _can_read_anything(actor)
    if not base:
        return base
    if not roles.moderates(actor, post.thread.subforum):
        return deny("not a moderator of this sub-forum")
    if not roles.is_admin_or_owner(actor) and _escalated(post=post):
        return deny("escalated to Admins and Owners")
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


def _ready_for_review(invitation):
    from sponsorship.models import Invitation

    if invitation.status not in (Invitation.Status.ACCEPTED, Invitation.Status.WAITLISTED):
        return deny(f"invitation is {invitation.status}")
    from accounts.models import IdentityRecord

    if not IdentityRecord.objects.filter(user_id=invitation.invitee_id, submitted_at__isnull=False).exists():
        return deny("the invitee has not submitted their details")
    return allow()


@rule("invitation.approve")
def _invitation_approve(actor, invitation):
    """Admin manual review (design rule 17). Approving a waitlisted invitation is how leadership
    goes over a sponsor's cap."""
    if actor.status != User.Status.ACTIVE or not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners review invitations")
    return _ready_for_review(invitation)


@rule("invitation.decline")
def _invitation_decline(actor, invitation):
    return _invitation_approve(actor, invitation)


@rule("invitation.review_queue")
def _invitation_review_queue(actor, _target):
    if actor.status != User.Status.ACTIVE or not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners review invitations")
    return allow()


@rule("invitation.rescind")
def _invitation_rescind(actor, invitation):
    if invitation.sponsor_id != actor.pk:
        return deny("only the sponsor may rescind")
    if invitation.status not in invitation.LIVE:
        return deny(f"invitation is {invitation.status}")
    return allow()


@rule("invitation.invitee_decline")
def _invitation_invitee_decline(actor, invitation):
    """A signed-in invitee declining. Before acceptance the invitee declines on the token page."""
    if invitation.invitee_id != actor.pk:
        return deny("not your invitation")
    if invitation.status not in invitation.LIVE:
        return deny(f"invitation is {invitation.status}")
    return allow()


@rule("onboarding.submit_identity")
def _onboarding_submit_identity(actor, invitation):
    from allauth.mfa.models import Authenticator

    if actor.status != User.Status.INVITED or invitation.invitee_id != actor.pk:
        return deny("not an invitee awaiting review")
    if invitation.status not in (invitation.Status.ACCEPTED, invitation.Status.WAITLISTED):
        return deny(f"invitation is {invitation.status}")
    if not Authenticator.objects.filter(user=actor, type=Authenticator.Type.TOTP).exists():
        return deny("set up two-factor authentication first")
    from accounts.models import IdentityRecord

    if IdentityRecord.objects.filter(user=actor, submitted_at__isnull=False).exists():
        return deny("details already submitted")
    return allow()


@rule("subscription.comp")
def _subscription_comp(actor, member):
    """Complimentary membership moves a Guest to Provisional as payment would (design rule 20)."""
    if actor.status != User.Status.ACTIVE or not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners grant complimentary membership")
    role = roles.trust_role(member)
    if member.status != User.Status.GUEST or role is None or role.name != roles.GUEST:
        return deny("only a Guest can be moved to Provisional")
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


def _staff_scope_ok(actor, related_post, scope_subforums=()):
    """Moderators act within their sub-forums: a limited action names only ones they moderate;
    otherwise a scoped Moderator needs a related post in scope, and only a global Moderator acts on
    a member with neither."""
    if roles.is_admin_or_owner(actor):
        return True
    if not roles.is_moderator(actor):
        return False
    if scope_subforums:
        return all(roles.moderates(actor, sf) for sf in scope_subforums)
    if related_post is not None:
        return roles.moderates(actor, related_post.thread.subforum)
    return roles.moderated_subforum_ids(actor) is None


def _needs_leadership(kind, scope_subforums):
    """Rule 36: bans and Probation always, and a suspension or hold that is not limited to one
    sub-forum, need an Admin or Owner as initiator or approver."""
    return kind in ADMIN_APPROVAL_KINDS or kind not in MODERATOR_KINDS or (
        kind in SCOPABLE_KINDS and not scope_subforums
    )


@rule("moderation.initiate")
def _moderation_initiate(actor, request):
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    if request.scope_subforums and request.kind not in SCOPABLE_KINDS:
        return deny("only suspensions and holds are limited to a sub-forum")
    if not roles.is_admin_or_owner(actor):
        if request.kind not in MODERATOR_KINDS:
            return deny("only Admins and Owners may take this action")
        if not _staff_scope_ok(actor, request.related_post, request.scope_subforums):
            return deny("outside your moderation scope")
    return _staff_may_act_on(actor, request.target_user)


def _eligible_approver(actor, action):
    if action.status != action.Status.PENDING:
        return deny("already handled", code="handled")
    if action.initiated_by_id == actor.pk:
        return deny("approver must differ from initiator")
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    admin = roles.is_admin_or_owner(actor)
    if not admin and _escalated(related_action=action):
        return deny("escalated to Admins and Owners")
    scopes = tuple(action.scope_subforums.all())
    if _needs_leadership(action.kind, scopes) and not admin:
        return deny("this action needs an Admin or Owner to approve")
    if not admin and not _staff_scope_ok(actor, action.related_post, scopes):
        return deny("outside your moderation scope")
    return _staff_may_act_on(actor, action.target_user)


@rule("moderation.approve")
def _moderation_approve(actor, action):
    return _eligible_approver(actor, action)


@rule("moderation.decline")
def _moderation_decline(actor, action):
    """An eligible approver may decline a pending action, with a reason (rule 36)."""
    return _eligible_approver(actor, action)


@rule("moderation.withdraw")
def _moderation_withdraw(actor, action):
    if action.status != action.Status.PENDING:
        return deny("already handled", code="handled")
    if action.initiated_by_id != actor.pk:
        return deny("only the initiator withdraws an action")
    return allow()


@rule("moderation.lift_ban")
def _moderation_lift_ban(actor, ban):
    """A ban is lifted by the reversal payment (build step 5) or by an Admin or Owner."""
    if ban.kind != ban.Kind.BAN or ban.status != ban.Status.ACTIVE:
        return deny("not an active ban")
    if actor.status != User.Status.ACTIVE or not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners lift bans")
    return _staff_may_act_on(actor, ban.target_user)


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


def _promotable(member, role_name):
    """The member still holds the role being promoted from and is not banned or removed."""
    role = roles.trust_role(member)
    if role is None or role.name != role_name:
        return deny(f"member is no longer {role_name}")
    standing = _can_read_anything(member)
    if not standing:
        return deny(f"member's {standing.reason}")
    return allow()


@rule("promotion.recommend")
def _promotion_recommend(actor, member):
    from sponsorship.eligibility import full_promotion_eligible
    from sponsorship.models import Promotion

    if member.pk == actor.pk:
        return deny("cannot recommend yourself")
    if actor.status != User.Status.ACTIVE or roles.trust_rank(actor) < roles.rank_of(roles.FULL):
        return deny("only Full members and above recommend")
    promotable = _promotable(member, roles.PROVISIONAL)
    if not promotable:
        return promotable
    if Promotion.objects.open().filter(member=member).exists():
        return deny("member already has a promotion in progress")
    if not full_promotion_eligible(member):
        return deny("member is not yet eligible")
    return allow()


@rule("promotion.review")
def _promotion_review(actor, promotion):
    if promotion.status != promotion.Status.RECOMMENDED:
        return deny("promotion is not awaiting review")
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    if not (roles.is_moderator(actor) or roles.is_admin_or_owner(actor)):
        return deny("only Moderators review promotions")
    if not roles.is_admin_or_owner(actor) and _escalated(related_promotion=promotion):
        return deny("escalated to Admins and Owners")
    return _promotable(promotion.member, promotion.from_role.name)


@rule("promotion.decide")
def _promotion_decide(actor, promotion):
    if promotion.status != promotion.Status.REVIEWED:
        return deny("promotion has not been reviewed")
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    if not roles.is_admin_or_owner(actor):
        return deny("only Admins and Owners decide promotions")
    return _promotable(promotion.member, promotion.from_role.name)


@rule("promotion.tenured")
def _promotion_tenured(actor, member):
    """Full to Tenured: no recommendation step; an Admin or Moderator promotes."""
    from sponsorship.eligibility import tenured_promotion_eligible

    if member.pk == actor.pk:
        return deny("cannot promote yourself")
    if actor.status != User.Status.ACTIVE:
        return deny(f"account is {actor.status}")
    if not (roles.is_moderator(actor) or roles.is_admin_or_owner(actor)):
        return deny("only Admins and Moderators promote to Tenured")
    promotable = _promotable(member, roles.FULL)
    if not promotable:
        return promotable
    if not tenured_promotion_eligible(member):
        return deny("member is not yet eligible")
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
