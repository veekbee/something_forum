from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.html import escape, linebreaks

from audit import log
from boards.limits import should_hold
from boards.models import Post, PostRevision, Thread
from core.models import Notification
from core.services import require


def render_body(source):
    # Placeholder until the strict Markdown renderer lands with the editor (build step 3):
    # escape everything, keep paragraphs.
    return linebreaks(escape(source))


def _add_post(thread, author, body_source):
    now = timezone.now()
    post = Post.objects.create(
        thread=thread,
        author=author,
        body_source=body_source,
        body_html=render_body(body_source),
        created_at=now,
        is_held=should_hold(author, thread.subforum),
    )
    Thread.objects.filter(pk=thread.pk).update(post_count=F("post_count") + 1, last_post_at=now)
    return post


@transaction.atomic
def start_thread(author, subforum, title, body_source):
    require(author, "subforum.start_thread", subforum)
    thread = Thread.objects.create(subforum=subforum, kind=Thread.Kind.DISCUSSION, title=title, author=author)
    post = _add_post(thread, author, body_source)
    return thread, post


@transaction.atomic
def reply(author, thread, body_source):
    require(author, "thread.reply", thread)
    return _add_post(thread, author, body_source)


@transaction.atomic
def open_thread(actor, thread, ip=None):
    """Check that `actor` may read `thread`, auditing a Moderator's read of a DM under a grant
    (design rule 8). Returns the Decision."""
    decision = require(actor, "thread.read", thread)
    if decision.via == "dm_grant":
        log.record(actor, "dm.read_under_grant", thread, {"grant": decision.detail.pk}, ip=ip)
    return decision


@transaction.atomic
def release_post(actor, post):
    require(actor, "post.moderate", post)
    if not post.is_held:
        raise ValidationError("post is not held")
    post.is_held, post.released_by, post.released_at = False, actor, timezone.now()
    post.save(update_fields=["is_held", "released_by", "released_at"])
    log.record(actor, "post.release", post)
    return post


@transaction.atomic
def reject_post(actor, post, reason):
    """A rejected post leaves the thread, stops counting toward rate limits and held-post counts,
    and stays in its author's post history. The author is notified."""
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


@transaction.atomic
def edit_post(actor, post, body_source):
    """Every edit is kept as a PostRevision, visible to staff. The first edit also records the
    original text, so the revisions hold every version. Staff edits of others' posts are audited."""
    post = Post.objects.select_for_update().get(pk=post.pk)
    decision = require(actor, "post.edit", post)
    now = timezone.now()
    if not post.revisions.exists():
        PostRevision.objects.create(
            post=post, body_source=post.body_source, edited_by_id=post.author_id, edited_at=post.created_at
        )
    PostRevision.objects.create(post=post, body_source=body_source, edited_by=actor, edited_at=now)
    post.body_source, post.body_html, post.edited_at = body_source, render_body(body_source), now
    post.save(update_fields=["body_source", "body_html", "edited_at"])
    if decision.via == "staff" and post.author_id != actor.pk:
        log.record(actor, "post.edit", post, {"author": post.author_id})
    return post


@transaction.atomic
def delete_post(actor, post, reason=""):
    """Soft delete: the post stays, visible to Admins and Owners with who deleted it and why.
    Staff must give a reason for deleting someone else's post, and are audited."""
    post = Post.objects.select_for_update().get(pk=post.pk)
    decision = require(actor, "post.delete", post)
    by_staff = decision.via == "staff" and post.author_id != actor.pk
    if by_staff and not reason.strip():
        raise ValidationError("staff must give a reason for deleting a member's post")
    post.deleted_at, post.deleted_by, post.delete_reason = timezone.now(), actor, reason
    post.save(update_fields=["deleted_at", "deleted_by", "delete_reason"])
    if by_staff:
        log.record(actor, "post.delete", post, {"author": post.author_id, "reason": reason})
    return post


def _set_thread(actor, thread, action, **fields):
    thread = Thread.objects.select_for_update().get(pk=thread.pk)
    require(actor, action, thread)
    for name, value in fields.items():
        setattr(thread, name, value)
    thread.save(update_fields=list(fields))
    log.record(actor, action, thread, {k: str(v) for k, v in fields.items()})
    return thread


@transaction.atomic
def set_locked(actor, thread, locked):
    state = Thread.State.LOCKED if locked else Thread.State.OPEN
    return _set_thread(actor, thread, "thread.lock", state=state)


@transaction.atomic
def set_pinned(actor, thread, pinned):
    return _set_thread(actor, thread, "thread.pin", is_pinned=pinned)


@transaction.atomic
def archive_thread(actor, thread):
    return _set_thread(actor, thread, "thread.archive", state=Thread.State.ARCHIVED)
