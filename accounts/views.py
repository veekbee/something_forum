from allauth.account.views import LogoutView as AllauthLogoutView


class LogoutView(AllauthLogoutView):
    """allauth's logout, plus the header that empties the browser's caches for this site and
    unregisters the service worker, which cannot see a logout itself (design rule 14)."""

    def post(self, *args, **kwargs):
        response = super().post(*args, **kwargs)
        response["Clear-Site-Data"] = '"cache", "storage"'
        return response
