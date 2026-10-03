from django.contrib import admin

from billing.models import Charge, Subscription
from core.admin_site import ReadOnlyAdmin

for model in (Subscription, Charge):
    admin.site.register(model, ReadOnlyAdmin)
