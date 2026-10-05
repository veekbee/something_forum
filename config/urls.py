from django.contrib import admin
from django.urls import include, path

from accounts import data_views
from accounts.views import LogoutView, leave_page, sessions_page
from core import install, views

urlpatterns = [
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("manifest.webmanifest", install.manifest, name="manifest"),
    path("sw.js", install.service_worker, name="service_worker"),
    path("icons/<str:name>.png", install.icon, name="icon"),
    path("offline/", views.offline, name="offline"),
    path("legal/<slug:slug>/", views.legal, name="legal"),
    path("help/install/", views.install_help, name="install_help"),
    path("sessions/", sessions_page, name="sessions"),
    path("leave/", leave_page, name="leave"),
    path("data/", data_views.your_data, name="your_data"),
    path("data/export/<int:pk>/download/", data_views.export_download, name="export_download"),
    path("data-export/<str:token>/", data_views.export_link, name="export_link"),
    path("staff/data/", data_views.data_requests, name="data_requests"),
    path("notifications/", views.notifications_page, name="notifications"),
    path("notifications/settings/", views.notification_settings, name="notification_settings"),
    path("notifications/read/", views.notifications_all_read, name="notifications_all_read"),
    path("notifications/<int:pk>/read/", views.notification_read, name="notification_read"),
    path("notifications/<int:pk>/open/", views.notification_open, name="notification_open"),
    path("staff/settings/", views.site_settings, name="site_settings"),
    path("staff/trace/", views.trace_watermark, name="trace_watermark"),
    # Same path as allauth's logout, listed first so this view handles it.
    path("accounts/logout/", LogoutView.as_view()),
    path("accounts/", include("allauth.urls")),
    path("staff/admin/", admin.site.urls),
    path("", include("sponsorship.urls")),
    path("", include("billing.urls")),
    path("", include("moderation.urls")),
    path("", include("boards.urls")),
]
