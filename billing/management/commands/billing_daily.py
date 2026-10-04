from django.core.management.base import BaseCommand

from billing.lapse import run_daily


class Command(BaseCommand):
    help = "Billing reminders, comp endings, read-only on lapse and the restriction stage. Run daily."

    def handle(self, *args, **options):
        result = run_daily()
        self.stdout.write(", ".join(f"{k}: {v}" for k, v in result.items()))
