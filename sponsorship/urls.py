from django.urls import path

from sponsorship import views

urlpatterns = [
    path("invitations/", views.invitations, name="invitations"),
    path("invitations/<int:pk>/rescind/", views.rescind, name="invitation_rescind"),
    # Under /invitations/accept/, the public prefix in settings.PUBLIC_PATH_PREFIXES.
    path("invitations/accept/<str:token>/", views.accept, name="invitation_accept"),
    path("invitations/accept/<str:token>/decline/", views.decline_link, name="invitation_decline_link"),
    path("onboarding/", views.onboarding_status, name="onboarding_status"),
    path("onboarding/decline/", views.onboarding_decline, name="onboarding_decline"),
    path("staff/onboarding/", views.review_queue, name="review_queue"),
    path("staff/onboarding/<int:pk>/approve/", views.review_approve, name="review_approve"),
    path("staff/onboarding/<int:pk>/decline/", views.review_decline, name="review_decline"),
    path("staff/guests/<int:user_pk>/comp/", views.comp_guest, name="comp_guest"),
    path("sponsorship/transfer/", views.transfer_status, name="transfer_status"),
    path("sponsorship/vouch/<slug:slug>/", views.vouch, name="vouch"),
    path("sponsorship/requests/", views.request_vouch, name="request_vouch"),
    path("sponsorship/requests/<int:pk>/<str:step>/", views.vouch_request_action, name="vouch_request_action"),
    path("sponsorship/offers/<int:pk>/<str:step>/", views.offer_action, name="offer_action"),
    path("staff/transfers/<int:pk>/<str:step>/", views.transfer_decide, name="transfer_decide"),
]
