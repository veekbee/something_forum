from django.contrib import admin
from django.urls import include, path

from accounts.views import LogoutView
from core import views

urlpatterns = [
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("notifications/", views.notifications_page, name="notifications"),
    path("notifications/settings/", views.notification_settings, name="notification_settings"),
    # Same path as allauth's logout, listed first so this view handles it.
    path("accounts/logout/", LogoutView.as_view()),
    path("accounts/", include("allauth.urls")),
    path("staff/admin/", admin.site.urls),
    path("", include("sponsorship.urls")),
    path("", include("moderation.urls")),
    path("", include("boards.urls")),
]
