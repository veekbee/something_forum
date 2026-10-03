from django.contrib import admin
from django.contrib.auth.views import redirect_to_login

from core.permissions import can


class ForumAdminSite(admin.AdminSite):
    """Read-only inspection for Admins and Owners. Access is decided by the permission service,
    not Django's is_staff flag, and sign-in goes through allauth so TOTP applies."""

    site_header = "Forum staff admin"
    site_title = "Forum staff admin"
    index_title = "Inspect data"

    def has_permission(self, request):
        return bool(can(request.user, "admin_site.view"))

    def login(self, request, extra_context=None):
        return redirect_to_login(request.get_full_path())


class ReadOnlyAdmin(admin.ModelAdmin):
    """Changes go through services, so they are permission-checked and audited."""

    def has_module_permission(self, request):
        return bool(can(request.user, "admin_site.view"))

    def has_view_permission(self, request, obj=None):
        return bool(can(request.user, "admin_site.view"))

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
