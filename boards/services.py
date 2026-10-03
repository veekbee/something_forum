from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.html import escape, linebreaks

from audit import log
from boards.limits import should_hold
from boards.models import Post, Thread
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
    """A rejected post stays hidden and stops counting toward rate limits and held-post counts."""
    require(actor, "post.moderate", post)
    if not post.is_held:
        raise ValidationError("post is not held")
    post.is_held, post.rejected_by, post.rejected_at = False, actor, timezone.now()
    post.save(update_fields=["is_held", "rejected_by", "rejected_at"])
    log.record(actor, "post.reject", post, {"reason": reason})
    return post
