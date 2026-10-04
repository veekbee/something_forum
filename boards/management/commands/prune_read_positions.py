from django.core.management.base import BaseCommand

from boards.reading import prune


class Command(BaseCommand):
    help = "Delete read positions on threads untouched for reading.prune_after_days. Run daily."

    def handle(self, *args, **options):
        self.stdout.write(f"Read positions deleted: {prune()}.")
