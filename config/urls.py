from django.contrib import admin
from django.urls import include, path

from core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("accounts/", include("allauth.urls")),
    path("staff/admin/", admin.site.urls),
]
