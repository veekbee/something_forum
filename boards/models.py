from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core import registry


class SubForum(models.Model):
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)
    # Overrides of registry keys with sub-forum scope; missing keys fall back (core.registry).
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name

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


class ThreadParticipant(models.Model):
    """DM membership, per-user read position, and thread subscriptions."""

    thread = models.ForeignKey(Thread, on_delete=models.PROTECT, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="thread_participations")
    joined_at = models.DateTimeField(default=timezone.now)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["thread", "user"], name="one_participation_per_thread")]


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
    body_source = models.TextField()
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    edited_at = models.DateTimeField(default=timezone.now)


class Attachment(models.Model):
    """Served only through a short-lived signed URL after a permission check on the post."""

    post = models.ForeignKey(Post, on_delete=models.PROTECT, related_name="attachments")
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    storage_key = models.CharField(max_length=512, unique=True)
    filename = models.CharField(max_length=255)
    mime = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
