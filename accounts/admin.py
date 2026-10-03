from django.contrib import admin

from accounts.models import Role, RoleAssignment, User, UserSession
from core.admin_site import ReadOnlyAdmin

# IdentityRecord is deliberately not registered: identity data is read through its own
# permission check (identity.read), not alongside other records.


@admin.register(User)
class UserAdmin(ReadOnlyAdmin):
    list_display = ["display_name", "email", "status", "joined_at"]
    list_filter = ["status"]
    search_fields = ["display_name", "email"]
    exclude = ["password"]


@admin.register(Role)
class RoleAdmin(ReadOnlyAdmin):
    list_display = ["name", "rank", "is_staff"]


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(ReadOnlyAdmin):
    list_display = ["user", "role", "scope_subforum", "granted_at", "revoked_at"]


admin.site.register(UserSession, ReadOnlyAdmin)
