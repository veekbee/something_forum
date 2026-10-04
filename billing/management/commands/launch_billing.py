from django.core.management.base import BaseCommand

from billing.lapse import launch


class Command(BaseCommand):
    help = ("Run once, when billing goes live: comps other than staff comps become founding comps "
            "ending billing.founding_comp_months later.")

    def handle(self, *args, **options):
        self.stdout.write(f"Founding comps: {launch()}.")
