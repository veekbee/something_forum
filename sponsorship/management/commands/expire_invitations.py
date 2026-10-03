from django.core.management.base import BaseCommand

from sponsorship.onboarding import expire_pending


class Command(BaseCommand):
    help = "Expire invitations nobody accepted within invitation.expiry_days. Run daily."

    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_pending()} invitation(s).")
