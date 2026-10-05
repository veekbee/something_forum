"""The data migration giving earlier thread starters a follow (docs/DESIGN.md, Following a thread)."""

from datetime import timedelta

from boards.follows import AUTOMATIC_FOLLOWS_SINCE, follow_earlier_starters
from boards.models import Thread, ThreadParticipant
from tests.factories import make_dm, make_thread


def _follows(thread):
    return ThreadParticipant.objects.filter(thread=thread, user=thread.author).count()


def test_earlier_starters_get_a_follow_and_nothing_else_changes(make_user, general):
    before = AUTOMATIC_FOLLOWS_SINCE - timedelta(days=1)
    after = AUTOMATIC_FOLLOWS_SINCE + timedelta(days=1)
    old = make_thread(general, make_user("full"), created_at=before)
    old_followed = make_thread(general, make_user("full"), created_at=before)
    ThreadParticipant.objects.create(thread=old_followed, user=old_followed.author)
    unfollowed = make_thread(general, make_user("full"), created_at=after)  # no row: its starter unfollowed
    conversation = make_dm(make_user("full"), make_user("full"))
    Thread.objects.filter(pk=conversation.pk).update(created_at=before)
    participants = ThreadParticipant.objects.filter(thread=conversation).count()

    assert follow_earlier_starters(Thread, ThreadParticipant) == 1
    assert _follows(old) == 1
    assert ThreadParticipant.objects.get(thread=old, user=old.author).joined_at == old.created_at
    assert _follows(old_followed) == 1 and _follows(unfollowed) == 0
    assert ThreadParticipant.objects.filter(thread=conversation).count() == participants
    assert follow_earlier_starters(Thread, ThreadParticipant) == 0  # running again adds nothing


def test_the_cutoff_is_the_commit_that_brought_automatic_follows():
    assert AUTOMATIC_FOLLOWS_SINCE.isoformat() == "2026-10-04T03:00:26+00:00"
