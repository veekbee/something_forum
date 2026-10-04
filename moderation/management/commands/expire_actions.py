from django.core.management.base import BaseCommand

from moderation.services import expire_actions


class Command(BaseCommand):
    help = "Mark time-limited moderation actions past their end as expired. Run daily."

    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_actions()} action(s).")
