"""Follows for threads started before following existed (docs/DESIGN.md, Following a thread).

Starters have followed their new threads automatically since the step 4 corrections (commit 5cc0afa,
3 Oct 2026, 21:00 -06:00). An older thread with no follow row for its starter never had one, so it
gets one. A newer thread with no row means the starter unfollowed it, so it is left alone. (A starter
who followed and then unfollowed an older thread cannot be told apart; there were no real members
then.)"""

from datetime import datetime, timezone

AUTOMATIC_FOLLOWS_SINCE = datetime(2026, 10, 4, 3, 0, 26, tzinfo=timezone.utc)


def follow_earlier_starters(Thread, ThreadParticipant, since=AUTOMATIC_FOLLOWS_SINCE):
    """Works with real or historical models. Returns how many follows it added."""
    added = 0
    for thread in Thread.objects.filter(kind="discussion", created_at__lt=since).only("pk", "author_id", "created_at"):
        if ThreadParticipant.objects.filter(thread_id=thread.pk, user_id=thread.author_id).exists():
            continue
        ThreadParticipant.objects.create(thread_id=thread.pk, user_id=thread.author_id, joined_at=thread.created_at)
        added += 1
    return added
