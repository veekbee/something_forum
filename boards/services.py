"""Forum writes. Each service checks permission through core.permissions, does the work in one
transaction, and audits staff actions there (design rules 1 and 10)."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts import roles
from audit import log
from boards import dm, images
from boards.limits import should_hold
from boards.models import Post, PostQuote, PostRevision, SubForum, Thread, ThreadParticipant, ThreadTitleRevision
from boards.rendering import render_post
from core import registry
from core.models import Notification
from core.permissions import MoveRequest, can
from core.services import require

# --- rendering, quotes, mentions -------------------------------------------------------------


def _render(post, validate_quotes=True, collect_mentions=True):
    rendered = render_post(
        post, attachments=post.attachments.order_by("pk"), validate_quotes=validate_quotes,
        collect_mentions=collect_mentions,
    )
    post.body_html = rendered.html
    post.save(update_fields=["body_html"])
    if validate_quotes:
        PostQuote.objects.filter(quoting_post=post).exclude(quoted_post_id__in=rendered.quoted_ids).delete()
        for quoted_id in rendered.quoted_ids:
            PostQuote.objects.get_or_create(quoting_post=post, quoted_post_id=quoted_id)
    return rendered


def rerender_quoting(post):
    """Rule 24: when a quoted post is edited, deleted or anonymised, the posts quoting it change too."""
    for quote in PostQuote.objects.filter(quoted_post=post).select_related("quoting_post__thread__subforum"):
        _render(quote.quoting_post, validate_quotes=False, collect_mentions=False)


def notify_mentions(post, users):
    """Rule 23: only members who can read the thread, only once the post is visible, and at most
    mentions.max_notified_per_post of them."""
    if post.is_held or post.rejected_at is not None or post.deleted_at is not None:
        return []
    limit = registry.site_value("mentions.max_notified_per_post")
    notified = []
    for user in users:
        if len(notified) >= limit:
            break
        if user.pk == post.author_id or not can(user, "thread.read", post.thread):
            continue
        if dm.has_blocked(user, post.author):
            continue
        Notification.objects.create(
            recipient=user, kind="mention", payload={"post": post.pk, "thread": post.thread_id}
        )
        notified.append(user)
    return notified


def notify_followers(post):
    """Followers of a forum thread hear of a visible new post: one unread notice per thread, and
    only for those who can still read it and have not blocked the author."""
    thread = post.thread
    if thread.kind != Thread.Kind.DISCUSSION:
        return
    for follow in ThreadParticipant.objects.filter(thread=thread).exclude(user=post.author_id).select_related("user"):
        follower = follow.user
        if Notification.objects.filter(recipient=follower, kind="thread.reply", read_at__isnull=True,
                                       payload__thread=thread.pk).exists():
            continue
        if not can(follower, "post.read", post) or dm.has_blocked(follower, post.author):
            continue
        Notification.objects.create(recipient=follower, kind="thread.reply", payload={"thread": thread.pk, "post": post.pk})


@transaction.atomic
def set_following(actor, thread, follow):
    require(actor, "thread.follow", thread)
    if follow:
        ThreadParticipant.objects.get_or_create(thread=thread, user=actor, left_at=None)
    else:
        ThreadParticipant.objects.filter(thread=thread, user=actor).delete()


def _attach(post, author, files):
    for upload in files:
        images.store(post, author, upload)


# --- posting ---------------------------------------------------------------------------------


def _add_post(thread, author, body_source, files):
    images.check_caps(thread.subforum, files)
    now = timezone.now()
    held = should_hold(author, thread.subforum)
    post = Post.objects.create(
        thread=thread, author=author, body_source=body_source, body_html="", created_at=now, is_held=held
    )
    _attach(post, author, files)
    rendered = _render(post)
    # A held post is not thread activity until it is released.
    if not held:
        Thread.objects.filter(pk=thread.pk).update(post_count=F("post_count") + 1, last_post_at=now)
        notify_followers(post)
    notify_mentions(post, rendered.mentions)
    if thread.kind == Thread.Kind.DM:
        from boards.messages import notify_new_message

        notify_new_message(post)
    return post


def _check_posting(author, action, target, subforum):
    """Refuse before the write transaction starts, so a rate-limit refusal can be counted toward the
    automatic flag without being rolled back with the refused post."""
    decision = can(author, action, target)
    if not decision:
        if decision.code == "rate_limit":
            from moderation.flags import record_rate_limit_refusal

            record_rate_limit_refusal(author, subforum)
        raise PermissionDenied(decision.reason)


def start_thread(author, subforum, title, body_source, files=()):
    _check_posting(author, "subforum.start_thread", subforum, subforum)
    return _start_thread(author, subforum, title, body_source, files)


@transaction.atomic
def _start_thread(author, subforum, title, body_source, files):
    require(author, "subforum.start_thread", subforum)
    title = title.strip()
    if not title:
        raise ValidationError("A thread needs a title.")
    thread = Thread.objects.create(
        subforum=subforum, kind=Thread.Kind.DISCUSSION, title=title, author=author, last_post_at=timezone.now()
    )
    # Starters follow their threads automatically and can unfollow; replying follows nothing.
    ThreadParticipant.objects.create(thread=thread, user=author)
    post = _add_post(thread, author, body_source, files)
    return thread, post


def reply(author, thread, body_source, files=()):
    _check_posting(author, "thread.reply", thread, thread.subforum)
    return _reply(author, thread, body_source, files)


@transaction.atomic
def _reply(author, thread, body_source, files):
    require(author, "thread.reply", thread)
    return _add_post(thread, author, body_source, files)


@transaction.atomic
def open_thread(actor, thread, ip=None):
    """Check that `actor` may read `thread`. Every DM read by someone other than a participant is
    audited: an Admin's or Owner's, and a Moderator's under a grant (design rule 8)."""
    decision = require(actor, "thread.read", thread)
    if decision.via == "dm_grant":
        log.record(actor, "dm.read_under_grant", thread, {"grant": decision.detail.pk}, ip=ip)
    elif decision.via == "admin" and thread.kind == Thread.Kind.DM:
        log.record(actor, "dm.read", thread, ip=ip)
    return decision


@transaction.atomic
def release_post(actor, post):
    require(actor, "post.moderate", post)
    if not post.is_held:
        raise ValidationError("post is not held")
    now = timezone.now()
    post.is_held, post.released_by, post.released_at = False, actor, now
    post.save(update_fields=["is_held", "released_by", "released_at"])
    Thread.objects.filter(pk=post.thread_id).update(post_count=F("post_count") + 1, last_post_at=now)
    log.record(actor, "post.release", post)
    rendered = render_post(post, attachments=post.attachments.order_by("pk"), validate_quotes=False)
    notify_mentions(post, rendered.mentions)
    notify_followers(post)
    return post


@transaction.atomic
def reject_post(actor, post, reason):
    """A rejected post leaves the thread, stops counting toward rate limits and held-post counts,
    and stays in its author's post history. The author is notified; mentions never are."""
    require(actor, "post.moderate", post)
    if not post.is_held:
        raise ValidationError("post is not held")
    post.is_held, post.rejected_by, post.rejected_at = False, actor, timezone.now()
    post.save(update_fields=["is_held", "rejected_by", "rejected_at"])
    log.record(actor, "post.reject", post, {"reason": reason})
    Notification.objects.create(
        recipient_id=post.author_id, kind="post.rejected", payload={"post": post.pk, "thread": post.thread_id}
    )
    return post


# --- editing and deleting --------------------------------------------------------------------


@transaction.atomic
def edit_post(actor, post, body_source, files=()):
    """Every edit is kept as a PostRevision, visible to staff; the first edit also records the
    original. A staff edit shows as one, and an Admin's edit in the Graveyard is a redaction.
    Posts quoting this one are re-rendered; newly added mentions are notified."""
    post = Post.objects.select_for_update(of=("self",)).select_related("thread__subforum").get(pk=post.pk)
    decision = require(actor, "post.edit", post)
    if files:
        images.check_caps(post.thread.subforum, files, existing=post.attachments.count())
    now = timezone.now()
    before = render_post(post, attachments=post.attachments.order_by("pk"), validate_quotes=False)
    if not post.revisions.exists():
        PostRevision.objects.create(
            post=post, body_source=post.body_source, edited_by_id=post.author_id, edited_at=post.created_at
        )
    redaction = decision.via == "redaction"
    PostRevision.objects.create(post=post, body_source=body_source, edited_by=actor, edited_at=now, is_redaction=redaction)
    post.body_source, post.edited_at = body_source, now
    post.save(update_fields=["body_source", "edited_at"])
    _attach(post, actor, files)
    rendered = _render(post, validate_quotes=not redaction)
    rerender_quoting(post)
    if redaction:
        log.record(actor, "post.redact", post, {"author": post.author_id})
    elif decision.via == "staff" and post.author_id != actor.pk:
        log.record(actor, "post.edit", post, {"author": post.author_id})
    already = {u.pk for u in before.mentions}
    notify_mentions(post, [u for u in rendered.mentions if u.pk not in already])
    return post


@transaction.atomic
def delete_post(actor, post, reason_key="", note=""):
    """Soft delete: the post stays, visible to Admins and Owners with who deleted it and why.
    Staff deleting someone else's post is "hiding" it (rule 35): a preset reason is required, the
    author is told, and the post stays in their history marked removed. An author deleting their
    own posts quickly may raise the rapid-deletion flag."""
    from moderation.models import reason_text

    post = Post.objects.select_for_update(of=("self",)).select_related("thread__subforum").get(pk=post.pk)
    decision = require(actor, "post.delete", post)
    hidden = decision.via == "staff" and post.author_id != actor.pk
    reason = reason_text(reason_key, note) if hidden else ""
    post.deleted_at, post.deleted_by, post.delete_reason = timezone.now(), actor, reason
    post.save(update_fields=["deleted_at", "deleted_by", "delete_reason"])
    rerender_quoting(post)
    if hidden:
        log.record(actor, "post.hide", post, {"author": post.author_id, "reason": reason})
        Notification.objects.create(
            recipient_id=post.author_id, kind="post.hidden",
            payload={"post": post.pk, "thread": post.thread_id, "reason": reason},
        )
    elif post.author_id == actor.pk:
        from moderation.flags import check_rapid_deletions

        check_rapid_deletions(actor)
    return post


hide_post = delete_post


@transaction.atomic
def purge_revisions(actor, post):
    """An Owner empties every earlier version of a Graveyard post, so redacted text survives
    nowhere. The current version and the record of who purged stay."""
    post = Post.objects.select_for_update(of=("self",)).select_related("thread__subforum").get(pk=post.pk)
    require(actor, "post.purge_revisions", post)
    now = timezone.now()
    latest = post.revisions.order_by("-edited_at", "-pk").first()
    earlier = post.revisions.exclude(pk=latest.pk if latest else None).filter(purged_at__isnull=True)
    count = earlier.update(body_source="", purged_by=actor, purged_at=now)
    log.record(actor, "post.purge_revisions", post, {"revisions": count})
    return count


# --- thread titles, states, moves and endings ------------------------------------------------


def _locked_thread(thread):
    return Thread.objects.select_for_update(of=("self",)).select_related("subforum").get(pk=thread.pk)


@transaction.atomic
def edit_title(actor, thread, title):
    thread = _locked_thread(thread)
    decision = require(actor, "thread.edit_title", thread)
    title = title.strip()
    if not title:
        raise ValidationError("A thread needs a title.")
    now = timezone.now()
    if not thread.title_revisions.exists():
        ThreadTitleRevision.objects.create(
            thread=thread, title=thread.title, edited_by_id=thread.author_id, edited_at=thread.created_at
        )
    ThreadTitleRevision.objects.create(thread=thread, title=title, edited_by=actor, edited_at=now)
    thread.title = title
    thread.save(update_fields=["title"])
    if decision.via in ("staff", "redaction") and thread.author_id != actor.pk:
        log.record(actor, f"thread.title_{'redact' if decision.via == 'redaction' else 'edit'}", thread)
    return thread


def _set_thread(actor, thread, action, payload=None, **fields):
    thread = _locked_thread(thread)
    require(actor, action, thread)
    for name, value in fields.items():
        setattr(thread, name, value)
    thread.save(update_fields=list(fields))
    log.record(actor, action, thread, payload or {k: str(v) for k, v in fields.items()})
    return thread


@transaction.atomic
def set_locked(actor, thread, locked):
    state = Thread.State.LOCKED if locked else Thread.State.OPEN
    return _set_thread(actor, thread, "thread.lock", state=state)


@transaction.atomic
def set_pinned(actor, thread, pinned):
    return _set_thread(actor, thread, "thread.pin", is_pinned=pinned)


def _effective_min_read_rank(subforum):
    rank, node = 0, subforum
    while node is not None:
        rank = max(rank, roles.rank_of(node.setting("subforum.min_read_role")))
        node = node.parent
    return rank


def widens_audience(source, destination):
    """True when members who cannot read `source` could read `destination` (rule 26)."""
    return _effective_min_read_rank(destination) < _effective_min_read_rank(source)


def _end(actor, thread, action, area_kind, reason):
    now = timezone.now()
    fields = {"state": Thread.State.ARCHIVED, "ended_by": actor, "ended_at": now, "end_reason": reason,
              "is_pinned": False}
    payload = {"from": thread.subforum_id, "reason": reason}
    if area_kind is not None:
        area = SubForum.objects.get(kind=area_kind)
        fields.update(origin_subforum=thread.subforum, subforum=area)
        payload["widened_audience"] = widens_audience(thread.subforum, area)
    return _set_thread(actor, thread, action, payload=payload, **fields)


@transaction.atomic
def archive_thread(actor, thread, reason=""):
    return _end(actor, thread, "thread.archive", None, reason)


@transaction.atomic
def send_to_graveyard(actor, thread, reason):
    if not reason.strip():
        raise ValidationError("Give a reason for sending a thread to the Graveyard.")
    return _end(actor, thread, "thread.graveyard", SubForum.Kind.GRAVEYARD, reason)


@transaction.atomic
def send_to_classics(actor, thread, reason=""):
    return _end(actor, thread, "thread.classics", SubForum.Kind.CLASSICS, reason)


@transaction.atomic
def move_thread(actor, thread, destination):
    thread = _locked_thread(thread)
    require(actor, "thread.move", MoveRequest(thread, destination))
    source = thread.subforum
    thread.subforum = destination
    thread.save(update_fields=["subforum"])
    log.record(actor, "thread.move", thread, {
        "from": source.pk, "to": destination.pk, "widened_audience": widens_audience(source, destination),
    })
    return thread


@transaction.atomic
def restore_thread(actor, thread):
    """Owner only: unarchive in place, or bring a thread back from the Graveyard or the Classics
    to the sub-forum it came from."""
    thread = _locked_thread(thread)
    require(actor, "thread.restore", thread)
    payload = {"from": thread.subforum_id, "to": thread.origin_subforum_id or thread.subforum_id}
    if thread.origin_subforum_id:
        thread.subforum_id = thread.origin_subforum_id
    thread.state, thread.origin_subforum = Thread.State.OPEN, None
    thread.ended_by, thread.ended_at, thread.end_reason = None, None, ""
    thread.save(update_fields=["subforum", "state", "origin_subforum", "ended_by", "ended_at", "end_reason"])
    log.record(actor, "thread.restore", thread, payload)
    return thread
