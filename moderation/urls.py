from django.urls import path

from moderation import views

urlpatterns = [
    path("staff/queue/", views.queue_page, name="queue"),
    path("staff/reports/<int:pk>/<str:action>/", views.report_action, name="report_action"),
    path("staff/queue/escalate/<str:type_>/<int:pk>/", views.escalate_item, name="escalate_item"),
    path("staff/actions/<int:pk>/approve/", views.approve_action, name="approve_action"),
    path("staff/actions/<int:pk>/decline/", views.decline_action, name="decline_action"),
    path("staff/actions/<int:pk>/withdraw/", views.withdraw_action, name="withdraw_action"),
    path("staff/actions/<int:pk>/lift/", views.lift_ban, name="lift_ban"),
    path("staff/actions/<int:pk>/annul/", views.annul_permanent_ban, name="annul_permanent_ban"),
    path("staff/members/<slug:slug>/act/", views.take_action, name="take_action"),
    path("staff/members/<slug:slug>/", views.member_view, name="staff_member"),
    path("staff/audit/", views.audit_log, name="audit_log"),
    path("staff/feed/", views.feed_page, name="feed"),
    path("staff/members/<slug:slug>/reset-extra/", views.reset_extra, name="reset_extra"),
    path("staff/entitlements/<int:pk>/revoke/", views.revoke_extra, name="revoke_extra"),
    path("staff/promotions/<int:pk>/<str:step>/", views.promotion_action, name="promotion_action"),
    path("staff/sponsor-reviews/<int:pk>/", views.sponsor_review_action, name="sponsor_review_action"),
    path("report/post/<int:pk>/", views.report_post, name="report_post"),
    path("report/member/<slug:slug>/", views.report_member, name="report_member"),
]
