from django.contrib.admin.apps import AdminConfig


class ForumAdminConfig(AdminConfig):
    default_site = "core.admin_site.ForumAdminSite"
