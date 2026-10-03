from django.contrib import admin

from audit.models import AuditEntry
from core.admin_site import ReadOnlyAdmin


@admin.register(AuditEntry)
class AuditEntryAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "actor", "action", "target_type", "target_id"]
    list_filter = ["action"]
