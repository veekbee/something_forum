from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core import registry


class SubForum(models.Model):
    class Kind(models.TextChoices):
        REGULAR = "regular"
        # Read-only areas on the forum index where threads end (docs/DESIGN.md, Thread endings).
        GRAVEYARD = "graveyard"
        CLASSICS = "classics"

    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.REGULAR)
    # Overrides of registry keys with sub-forum scope; missing keys fall back (core.registry).
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["position", "name"]
        constraints = [
            models.UniqueConstraint(fields=["kind"], condition=~Q(kind="regular"), name="one_graveyard_one_classics")
        ]

    def __str__(self):
        return self.name

    @property
    def is_ending_area(self):
        return self.kind != self.Kind.REGULAR

    def clean(self):
        registry.validate_subforum_settings(self.settings)

    def save(self, *args, **kwargs):
        registry.validate_subforum_settings(self.settings)
        super().save(*args, **kwargs)

    def setting(self, key):
        return registry.subforum_value(self, key)


class Thread(models.Model):
    class Kind(models.TextChoices):
        DISCUSSION = "discussion"
        DM = "dm"

    class State(models.TextChoices):
        OPEN = "open"
        LOCKED = "locked"
        ARCHIVED = "archived"

    subforum = models.ForeignKey(SubForum, null=True, blank=True, on_delete=models.PROTECT, related_name="threads")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.DISCUSSION)
    title = models.CharField(max_length=200)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="threads_started")
    state = models.CharField(max_length=16, choices=State.choices, default=State.OPEN)
    is_pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    last_post_at = models.DateTimeField(default=timezone.now)
    post_count = models.PositiveIntegerField(default=0)
    # Set when a thread ends in the Graveyard or the Classics; origin_subforum is where it came from.
    origin_subforum = models.ForeignKey(
        SubForum, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(kind="dm", subforum__isnull=True) | Q(kind="discussion", subforum__isnull=False)),
                name="dm_threads_have_no_subforum",
            )
        ]
        indexes = [models.Index(fields=["subforum", "-last_post_at"])]

    def __str__(self):
        return self.title


class ThreadTitleRevision(models.Model):
    """Every title a thread has had; visible to staff."""

    thread = models.ForeignKey(Thread, on_delete=models.PROTECT, related_name="title_revisions")
    title = models.CharField(max_length=200)
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    edited_at = models.DateTimeField(default=timezone.now)


class ThreadParticipant(models.Model):
    """DM membership, per-user read position, and thread subscriptions. In a DM a participant reads
    messages from joined_at up to left_at (design rule 31); last_read_at is shown to nobody else."""

    thread = models.ForeignKey(Thread, on_delete=models.PROTECT, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="thread_participations")
    joined_at = models.DateTimeField(default=timezone.now)
    last_read_at = models.DateTimeField(null=True, blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    left_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_active(self):
        return self.left_at is None

    class Meta:
        # A member who leaves and is added back gets a new row, so each period they were in the
        # conversation stays readable to them and the gap does not.
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "user"], condition=Q(left_at__isnull=True), name="one_active_participation"
            )
        ]


class PostQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("posts are soft-deleted only")

    def counted(self):
        """Posts that count toward rate limits and the held-post count: everything except
        posts staff rejected. Posts the member deleted still count."""
        return self.filter(rejected_at__isnull=True)


class Post(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.PROTECT, related_name="posts")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="posts")
    body_source = models.TextField()
    body_html = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_held = models.BooleanField(default=False)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    released_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    delete_reason = models.TextField(blank=True)

    objects = PostQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["author", "created_at"]),
            models.Index(fields=["thread", "created_at"]),
            models.Index(fields=["created_at"], condition=Q(is_held=True), name="post_held_queue"),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError("posts are soft-deleted only")


class PostRevision(models.Model):
    post = models.ForeignKey(Post, on_delete=models.PROTECT, related_name="revisions")
    body_source = models.TextField(blank=True)
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    edited_at = models.DateTimeField(default=timezone.now)
    # A redaction removes offending content and shows "redacted by staff"; a staff edit is
    # housekeeping and shows "edited by staff" (rule 56). A redaction needs a preset reason, stored
    # in words as hiding stores its reason.
    is_redaction = models.BooleanField(default=False)
    redaction_reason = models.TextField(blank=True)
    # An Owner purge empties body_source on earlier revisions so removed text survives nowhere.
    purged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    purged_at = models.DateTimeField(null=True, blank=True)


class PostQuote(models.Model):
    """One row per quote, so editing or deleting the quoted post can re-render the posts quoting it."""

    quoting_post = models.ForeignKey(Post, on_delete=models.PROTECT, related_name="quotes_made")
    quoted_post = models.ForeignKey(Post, on_delete=models.PROTECT, related_name="quoted_by")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["quoting_post", "quoted_post"], name="one_quote_row_per_pair")]


class Attachment(models.Model):
    """Served only through a short-lived signed URL after a permission check on the post. An avatar
    belongs to no post (User.avatar) and is shown to any signed-in member."""

    post = models.ForeignKey(Post, null=True, blank=True, on_delete=models.PROTECT, related_name="attachments")
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    storage_key = models.CharField(max_length=512, unique=True)
    filename = models.CharField(max_length=255)
    mime = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
