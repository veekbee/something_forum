"""Querysets of what a member may see, matching the post.read and subforum.read rules in
core.permissions so that lists, search and post history never show more than a single read would.
Forum posts only; DMs arrive with build step 4."""

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import Exists, OuterRef, Q

from accounts import roles
from boards.models import Post, SubForum, Thread
from core.permissions import can


def moderated_subforums(actor):
    """Sub-forums `actor` moderates: every one for Admins and Owners and global Moderators."""
    return [sf for sf in SubForum.objects.select_related("parent") if roles.moderates(actor, sf)]


def readable_subforums(actor):
    return [sf for sf in SubForum.objects.select_related("parent") if can(actor, "subforum.read", sf)]


def visible_posts(actor):
    """Forum posts `actor` may read: released and not deleted, plus their own held posts, plus
    held and rejected posts in sub-forums they moderate. Admins and Owners also see deleted posts."""
    if not can(actor, "search.use"):
        return Post.objects.none()
    posts = Post.objects.filter(thread__kind=Thread.Kind.DISCUSSION)
    if roles.is_admin_or_owner(actor):
        return posts
    readable = readable_subforums(actor)
    moderated = [sf.pk for sf in readable if roles.moderates(actor, sf)]
    return posts.filter(thread__subforum__in=readable, deleted_at__isnull=True).filter(
        Q(is_held=False, rejected_at__isnull=True)
        | Q(is_held=True, author=actor)
        | Q(thread__subforum_id__in=moderated)
    )


def visible_threads(actor, subforum):
    """Threads in `subforum` with at least one post `actor` may read, pinned first, then by
    latest activity. A thread whose only post is held stays out of other members' lists."""
    if not can(actor, "subforum.read", subforum):
        return Thread.objects.none()
    has_visible = Exists(visible_posts(actor).filter(thread=OuterRef("pk")))
    return (
        Thread.objects.filter(subforum=subforum, kind=Thread.Kind.DISCUSSION)
        .filter(has_visible)
        .order_by("-is_pinned", "-last_post_at")
    )


def thread_posts(actor, thread):
    """Posts shown in the thread, oldest first. A deleted post that had been published keeps its
    place as a placeholder (rule 47); the page shows its text only to those allowed to read it.
    Rejected posts were never published and leave nothing; a rejected post is in its author's post
    history instead."""
    if not can(actor, "thread.read", thread):
        return Post.objects.none()
    placeholders = Post.objects.filter(
        thread=thread, deleted_at__isnull=False, is_held=False, rejected_at__isnull=True
    )
    return (visible_posts(actor).filter(thread=thread) | placeholders).order_by("created_at", "pk")


def post_history(viewer, member):
    """The list of posts on `member`'s profile. Members see all of their own posts, held, rejected
    and removed ones included; everyone else sees what they could read in the threads."""
    if viewer.pk == member.pk:
        if not can(viewer, "member.own_history", member):
            return Post.objects.none()
        # Their own posts, held and rejected ones included, and posts staff removed (marked as
        # such in the template); posts they deleted themselves are gone from the list.
        own = Post.objects.filter(author=member, thread__kind=Thread.Kind.DISCUSSION).exclude(
            deleted_at__isnull=False, deleted_by=member
        )
        return own.order_by("-created_at")
    return visible_posts(viewer).filter(author=member).order_by("-created_at")


def searchable_dm_posts(actor):
    """Rule 29: Admins and Owners search all DMs, deleted messages included; a Moderator searches
    the DMs of members covered by an unexpired grant they hold; nobody else's search includes DMs.
    Showing DM text in results counts as a read (see boards.views.search)."""
    from django.utils import timezone

    from accounts.models import User
    from boards.models import ThreadParticipant
    from moderation.models import DMAccessGrant

    dms = Post.objects.filter(thread__kind=Thread.Kind.DM)
    if actor.status != User.Status.ACTIVE:
        return Post.objects.none()
    if roles.is_admin_or_owner(actor):
        return dms
    if roles.is_moderator(actor):
        grants = DMAccessGrant.objects.filter(moderator=actor, revoked_at__isnull=True, expires_at__gt=timezone.now())
        subjects = User.objects.filter(pk__in=grants.values("subject_users")).values("pk")
        threads = ThreadParticipant.objects.filter(user__in=subjects).values("thread_id")
        return dms.filter(thread_id__in=threads, deleted_at__isnull=True)
    return Post.objects.none()


def search_posts(actor, text):
    """PostgreSQL full-text search over post bodies and thread titles, limited to what the searcher
    may read in the forum plus the DMs rule 29 opens to them, best match first."""
    query = SearchQuery(text, config="english", search_type="websearch")
    vector = SearchVector("thread__title", weight="A", config="english") + SearchVector(
        "body_source", weight="B", config="english"
    )
    return (
        (visible_posts(actor) | searchable_dm_posts(actor))
        .annotate(search=vector)
        .filter(search=query)
        .annotate(rank=SearchRank(vector, query))
        .order_by("-rank", "-created_at")
        .select_related("thread", "author")
    )
