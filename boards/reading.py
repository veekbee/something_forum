"""Read positions on discussion threads (docs/DESIGN.md, Read positions; rule 74).

A thread is unread for a member when its last visible post is newer than their ThreadRead mark or,
with no mark, when its last post is within reading.unread_window_days. last_post_at ignores held
posts until they are released, so a held post never makes a thread unread. A sub-forum is unread
when any thread in it is. The marks are the member's alone."""

from datetime import timedelta

from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone

from boards.models import Thread, ThreadRead
from core import registry


def with_marks(user, threads):
    marks = ThreadRead.objects.filter(user=user, thread=OuterRef("pk")).values("last_read_at")[:1]
    return threads.annotate(read_mark=Subquery(marks))


def unread(user, threads, now=None):
    """The threads in `threads` that are unread for `user`."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=registry.site_value("reading.unread_window_days"))
    return with_marks(user, threads).filter(last_post_at__isnull=False).filter(
        Q(read_mark__isnull=True, last_post_at__gte=cutoff) | Q(last_post_at__gt=F("read_mark"))
    )


def is_unread(thread, now=None):
    """For a thread annotated by with_marks."""
    if thread.last_post_at is None:
        return False
    if thread.read_mark is None:
        now = now or timezone.now()
        return thread.last_post_at >= now - timedelta(days=registry.site_value("reading.unread_window_days"))
    return thread.last_post_at > thread.read_mark


def mark_read(user, thread, at=None):
    if thread.kind != Thread.Kind.DISCUSSION:
        return
    ThreadRead.objects.update_or_create(user=user, thread=thread, defaults={"last_read_at": at or timezone.now()})


def mark_all_read(user, threads, now=None):
    """Mark every unread thread in `threads` as read now. Returns how many."""
    now = now or timezone.now()
    ids = list(unread(user, threads, now).values_list("pk", flat=True))
    ThreadRead.objects.bulk_create(
        [ThreadRead(user=user, thread_id=pk, last_read_at=now) for pk in ids],
        update_conflicts=True, unique_fields=["user", "thread"], update_fields=["last_read_at"],
    )
    return len(ids)


def prune(now=None):
    """Delete read positions on threads untouched for reading.prune_after_days."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=registry.site_value("reading.prune_after_days"))
    deleted, _ = ThreadRead.objects.filter(
        Q(thread__last_post_at__lt=cutoff) | Q(thread__last_post_at__isnull=True, thread__created_at__lt=cutoff)
    ).delete()
    return deleted
