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
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["recipient", "read_at"])]


class DataRequest(models.Model):
    class Kind(models.TextChoices):
        EXPORT = "export"
        ERASURE = "erasure"

    class Status(models.TextChoices):
        PENDING = "pending"
        IN_PROGRESS = "in_progress"
        COMPLETED = "completed"
        REJECTED = "rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="data_requests")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    requested_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    file_key = models.CharField(max_length=512, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
