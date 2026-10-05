from django.conf import settings
from django.db import models
from django.utils import timezone

from core import registry


class SiteSetting(models.Model):
    """A site-wide value for a registry key. Only Owners write (core.services.set_site_setting)."""

    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(null=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.key

    def clean(self):
        registry.validate(self.key, self.value, registry.SITE)

    def save(self, *args, **kwargs):
        registry.validate(self.key, self.value, registry.SITE)
        super().save(*args, **kwargs)


class Notification(models.Model):
    """In-app first. Email is only a pointer back to the forum and never carries content or a
    sender (rule 39); see core.notifications."""

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["recipient", "read_at"])]


class NotificationPreference(models.Model):
    """A member's choice to get email for an optional kind. A missing row means off; account kinds
    always email and have no row."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notification_preferences")
    kind = models.CharField(max_length=64)
    email = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "kind"], name="one_preference_per_kind")]


class DataRequest(models.Model):
    """A member's export or erasure request (docs/DESIGN.md, Privacy; rules 79 and 80). The row
    survives the erasure it records."""

    class Kind(models.TextChoices):
        EXPORT = "export"
        ERASURE = "erasure"

    class Status(models.TextChoices):
        OPEN = "open"
        DEFERRED = "deferred"
        WITHDRAWN = "withdrawn"
        # An export being built, or an erasure an Owner has set running; the job runner takes it.
        BUILDING = "building"
        # An export ready to download.
        READY = "ready"
        # An export whose file has been deleted, or an erasure that has run.
        DONE = "done"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="data_requests")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    requested_at = models.DateTimeField(default=timezone.now)
    # The Owner who opened it for a removed member, who cannot sign in.
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # When the member confirmed an erasure request with their authenticator.
    confirmed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    deferred_until = models.DateTimeField(null=True, blank=True)
    deferral_reason = models.TextField(blank=True)
    # Posts the member asked to have fully removed (ids of their own posts).
    posts_to_remove = models.JSONField(default=list, blank=True)
    file_key = models.CharField(max_length=512, blank=True)
    # The emailed single-use link for a removed member; only its hash is kept.
    link_token_hash = models.CharField(max_length=64, blank=True)
    link_expires_at = models.DateTimeField(null=True, blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    # Other members' messages in an export are watermarked with this seed, copied from the
    # requesting session (or new, for an Owner-opened request), so the tracing page finds it
    # after the session record is gone.
    watermark_seed = models.CharField(max_length=64, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        indexes = [models.Index(fields=["kind", "status"])]

    def __str__(self):
        return f"{self.kind} for {self.user_id} ({self.status})"


class JobRun(models.Model):
    """One run of a scheduled job by run_jobs (core.jobs). The last success decides catch-up; the
    failure count decides the Owner notification (rule 81)."""

    job = models.CharField(max_length=64)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    ok = models.BooleanField()
    error = models.TextField(blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["job", "ok", "started_at"])]

    def __str__(self):
        return f"{self.job} {self.started_at:%Y-%m-%d %H:%M} {'ok' if self.ok else 'failed'}"
