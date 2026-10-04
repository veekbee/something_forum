from django.urls import path

from moderation import views

urlpatterns = [
    path("staff/queue/", views.queue_page, name="queue"),
    path("staff/reports/<int:pk>/<str:action>/", views.report_action, name="report_action"),
    path("staff/actions/<int:pk>/approve/", views.approve_action, name="approve_action"),
    path("staff/promotions/<int:pk>/<str:step>/", views.promotion_action, name="promotion_action"),
    path("staff/sponsor-reviews/<int:pk>/", views.sponsor_review_action, name="sponsor_review_action"),
    path("report/post/<int:pk>/", views.report_post, name="report_post"),
    path("report/member/<slug:slug>/", views.report_member, name="report_member"),
]
