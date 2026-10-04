from django.urls import path

from billing import views

urlpatterns = [
    path("billing/", views.billing_page, name="billing"),
    path("billing/pay/", views.pay, name="billing_pay"),
    path("billing/ban/", views.ban_payment, name="ban_payment"),
    path("billing/manage/", views.manage, name="billing_manage"),
    # Under /billing/stripe/webhook/, a public prefix in settings.PUBLIC_PATH_PREFIXES.
    path("billing/stripe/webhook/", views.stripe_webhook, name="stripe_webhook"),
]
