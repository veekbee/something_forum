from django.contrib import admin

from core.admin_site import ReadOnlyAdmin
from moderation.models import DMAccessGrant, ModerationAction, Report, SponsorReview


@admin.register(ModerationAction)
class ModerationActionAdmin(ReadOnlyAdmin):
    list_display = ["target_user", "kind", "status", "initiated_by", "approved_by", "starts_at", "ends_at"]
    list_filter = ["kind", "status"]


@admin.register(SponsorReview)
class SponsorReviewAdmin(ReadOnlyAdmin):
    list_display = ["sponsor", "banned_member", "status", "outcome", "created_at"]


for model in (Report, DMAccessGrant):
    admin.site.register(model, ReadOnlyAdmin)
