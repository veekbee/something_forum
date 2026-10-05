import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import jobs


class Command(BaseCommand):
    help = ("Run the scheduled jobs: the frequent ones every five minutes and the daily ones at "
            "jobs.daily_hour_utc, catching up any missed day. The jobs service runs this.")

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one tick and exit.")

    def handle(self, *args, once=False, **options):
        while True:
            results = jobs.tick()
            for name, ok in results.items():
                self.stdout.write(f"{name}: {'ok' if ok else 'skipped' if ok is None else 'failed'}")
            if once:
                return
            now = timezone.now().timestamp()
            period = jobs.TICK.total_seconds()
            time.sleep(period - now % period)
