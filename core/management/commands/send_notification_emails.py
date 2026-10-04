from django.core.management.base import BaseCommand

from core.notifications import send_digests


class Command(BaseCommand):
    help = "Send the daily pointer emails for optional notification kinds members chose. Run daily."

    def handle(self, *args, **options):
        self.stdout.write(f"Sent {send_digests()} email(s).")
