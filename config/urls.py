from django.contrib import admin
from django.urls import include, path

from accounts.views import LogoutView
from core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    # Same path as allauth's logout, listed first so this view handles it.
    path("accounts/logout/", LogoutView.as_view()),
    path("accounts/", include("allauth.urls")),
    path("staff/admin/", admin.site.urls),
    path("", include("sponsorship.urls")),
]
