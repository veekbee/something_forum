from django.apps import AppConfig


class BillingConfig(AppConfig):
    name = "billing"

    def ready(self):
        # One-time Checkout purposes register their webhook handlers on import.
        from billing import bans, extras  # noqa: F401
