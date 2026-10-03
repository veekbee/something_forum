from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib import admin
from django.contrib.auth.models import Group

from core.admin_site import ReadOnlyAdmin
from core.models import DataRequest, Notification, SiteSetting

# Third-party admin pages use Django's own permission flags, which this project does not use;
# remove them so every staff page goes through the permission service. Authenticators in
# particular hold TOTP secrets.
for model in (Group, EmailAddress, Authenticator):
    if admin.site.is_registered(model):
        admin.site.unregister(model)


@admin.register(SiteSetting)
class SiteSettingAdmin(ReadOnlyAdmin):
    list_display = ["key", "value", "updated_by", "updated_at"]


admin.site.register(Notification, ReadOnlyAdmin)
admin.site.register(DataRequest, ReadOnlyAdmin)
