from django.urls import path

from billing import views

urlpatterns = [
    path("billing/", views.billing_page, name="billing"),
    path("billing/pay/", views.pay, name="billing_pay"),
    path("billing/ban/", views.ban_payment, name="ban_payment"),
    path("billing/gift/<int:user_pk>/", views.gift, name="gift"),
    path("billing/gifts/<int:pk>/accept/", views.accept_gift, name="accept_gift"),
    path("billing/extras/", views.extras_page, name="extras"),
    path("billing/extras/<slug:key>/", views.buy_extra, name="buy_extra"),
    path("profile/customise/", views.customise, name="customise"),
    path("billing/manage/", views.manage, name="billing_manage"),
    # Under /billing/stripe/webhook/, a public prefix in settings.PUBLIC_PATH_PREFIXES.
    path("billing/stripe/webhook/", views.stripe_webhook, name="stripe_webhook"),
]
