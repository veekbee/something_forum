from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out

        from accounts import sessions

        # Session binding (rules 57 to 59): record each sign-in and sign-out.
        user_logged_in.connect(sessions.on_logged_in, dispatch_uid="accounts.sessions.logged_in")
        user_logged_out.connect(sessions.on_logged_out, dispatch_uid="accounts.sessions.logged_out")
