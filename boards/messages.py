"""Direct messages (docs/DESIGN.md, Direct messages; rules 30 to 32). Replies, edits and deletes go
through boards.services like forum posts; this module holds what only conversations have."""

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from accounts.models import Block
from audit import log
from boards import services
from boards.models import Post, Thread, ThreadParticipant
from core.models import Notification
from core.permissions import Conversation, Membership, can
from core.services import require

NOTICE = (
    "Direct messages are private from other members, but not from the forum's Admins. Admins can read "
    "and search every message, including edited and deleted ones. Moderators can read messages only "
    "with an Admin's permission, which is recorded. There is no end-to-end encryption."
)
SHORT_NOTICE = "Admins can read all messages."


@transaction.atomic
def start(actor, others, subject, body, files=()):
    """Start a conversation with `others` and send its first message."""
    others = list({u.pk: u for u in others if u.pk != actor.pk}.values())
    require(actor, "dm.start", Conversation(tuple(others)))
    now = timezone.now()
    title = subject.strip() or "Conversation"
    thread = Thread.objects.create(kind=Thread.Kind.DM, title=title[:200], author=actor, created_at=now, last_post_at=now)
    ThreadParticipant.objects.create(thread=thread, user=actor, joined_at=now, last_read_at=now)
    for user in others:
        ThreadParticipant.objects.create(thread=thread, user=user, joined_at=now, added_by=actor)
    post = services.reply(actor, thread, body, files)
    return thread, post


@transaction.atomic
def add(actor, thread, user):
    """Any participant can add someone; the newcomer sees only messages from now on."""
    require(actor, "dm.add", Membership(thread, user))
    return ThreadParticipant.objects.create(thread=thread, user=user, joined_at=timezone.now(), added_by=actor)


@transaction.atomic
def leave(actor, thread):
    """The member keeps read-only access to what was said while they were in it."""
    require(actor, "dm.leave", thread)
    ThreadParticipant.objects.filter(thread=thread, user=actor, left_at__isnull=True).update(left_at=timezone.now())


@transaction.atomic
def remove(actor, thread, user):
    require(actor, "dm.remove", Membership(thread, user))
    ThreadParticipant.objects.filter(thread=thread, user=user, left_at__isnull=True).update(left_at=timezone.now())
    log.record(actor, "dm.remove", thread, {"user": user.pk})


def mark_read(actor, thread):
    """Moves the member's own read position (shown to nobody else) and clears their notices."""
    now = timezone.now()
    ThreadParticipant.objects.filter(thread=thread, user=actor, left_at__isnull=True).update(last_read_at=now)
    Notification.objects.filter(
        recipient=actor, kind="dm", read_at__isnull=True, payload__thread=thread.pk
    ).update(read_at=now)


def notify_new_message(post):
    """One unread "dm" notice per conversation per person, not one per message. The sender has read
    their own message, so their read position moves too."""
    thread = post.thread
    ThreadParticipant.objects.filter(thread=thread, user=post.author_id, left_at__isnull=True).update(
        last_read_at=post.created_at
    )
    for participation in thread.participants.filter(left_at__isnull=True).exclude(user=post.author_id):
        recipient = participation.user
        if Notification.objects.filter(
            recipient=recipient, kind="dm", read_at__isnull=True, payload__thread=thread.pk
        ).exists():
            continue
        Notification.objects.create(recipient=recipient, kind="dm", payload={"thread": thread.pk, "post": post.pk})


def conversations(actor):
    """The member's conversations, latest first, each annotated with whether it has unread messages."""
    mine = ThreadParticipant.objects.filter(thread=OuterRef("pk"), user=actor)
    unread = ThreadParticipant.objects.filter(
        thread=OuterRef("pk"), user=actor, left_at__isnull=True
    ).filter(Q(last_read_at__isnull=True) | Q(last_read_at__lt=OuterRef("last_post_at")))
    return (
        Thread.objects.filter(kind=Thread.Kind.DM)
        .filter(Exists(mine))
        .annotate(unread=Exists(unread))
        .order_by("-last_post_at")
    )


def messages(actor, thread):
    """Messages `actor` may read here, oldest first: a participant's own periods in the conversation;
    everything for Admins and Owners, deleted messages included; under a grant, all but deleted."""
    decision = can(actor, "thread.read", thread)
    if not decision:
        return Post.objects.none()
    posts = Post.objects.filter(thread=thread)
    if decision.via == "participant":
        window = Q()
        for period in decision.detail:
            q = Q(created_at__gte=period.joined_at)
            if period.left_at is not None:
                q &= Q(created_at__lte=period.left_at)
            window |= q
        posts = posts.filter(window, deleted_at__isnull=True)
    elif decision.via != "admin":
        posts = posts.filter(deleted_at__isnull=True)
    return posts.order_by("created_at", "pk")


@transaction.atomic
def block(actor, target):
    """The blocked member is not told. Staff cannot be blocked."""
    require(actor, "member.block", target)
    Block.objects.get_or_create(blocker=actor, blocked=target)


@transaction.atomic
def unblock(actor, target):
    Block.objects.filter(blocker=actor, blocked=target).delete()


def unread_count(actor):
    return conversations(actor).filter(unread=True).count()


def system_lines(viewer, thread):
    """Who added whom and who left, as (when, text) lines among the messages, rendered from the
    participant rows (docs/DESIGN.md, Group conversations). The people a conversation started with
    have no line. A participant sees only lines from their own periods in the conversation, as with
    messages; staff reading it see them all."""
    rows = list(thread.participants.select_related("user", "added_by"))
    periods = [(r.joined_at, r.left_at) for r in rows if r.user_id == viewer.pk]

    def visible(when):
        return not periods or any(start <= when and (end is None or when <= end) for start, end in periods)

    lines = []
    for row in rows:
        # The people a conversation began with joined when it was created.
        if row.added_by_id is not None and row.joined_at > thread.created_at and visible(row.joined_at):
            lines.append((row.joined_at, f"{row.added_by.display_name} added {row.user.display_name}"))
        if row.left_at is not None and visible(row.left_at):
            lines.append((row.left_at, f"{row.user.display_name} left"))
    return sorted(lines)
