from django.core.management.base import BaseCommand

from sponsorship.onboarding import delete_ended_accounts


class Command(BaseCommand):
    help = (
        "Delete invited accounts whose invitation ended without approval more than "
        "invitation.ended_account_deletion_days ago. Run daily."
    )

    def handle(self, *args, **options):
        self.stdout.write(f"Deleted {delete_ended_accounts()} account(s).")
