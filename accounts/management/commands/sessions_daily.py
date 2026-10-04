from django.core.management.base import BaseCommand

from accounts.sessions import run_daily


class Command(BaseCommand):
    help = ("End sessions past their lifetime or idle limit, and delete session records "
            "session.retention_days after they ended. Run daily.")

    def handle(self, *args, **options):
        result = run_daily()
        self.stdout.write(", ".join(f"{k}: {v}" for k, v in result.items()))
