from django.contrib import admin

from core.admin_site import ReadOnlyAdmin
from sponsorship.models import Invitation, Promotion, Sponsorship


@admin.register(Invitation)
class InvitationAdmin(ReadOnlyAdmin):
    list_display = ["invitee_email", "sponsor", "status", "created_at"]
    exclude = ["token_hash"]


@admin.register(Sponsorship)
class SponsorshipAdmin(ReadOnlyAdmin):
    list_display = ["member", "sponsor", "started_at", "ended_at", "end_reason", "is_original"]


admin.site.register(Promotion, ReadOnlyAdmin)
