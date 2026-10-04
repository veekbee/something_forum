from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.factor_reset import reset_owner


class Command(BaseCommand):
    help = ("Reset an Owner's second factor from the server, the way back in when no other Owner can do it "
            "(docs/DESIGN.md, Authentication). Audited.")

    def add_arguments(self, parser):
        parser.add_argument("email", help="The Owner's email address.")
        parser.add_argument("--note", required=True, help="Why, for the audit log.")

    def handle(self, *args, **options):
        try:
            owner = reset_owner(options["email"], options["note"])
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        self.stdout.write(f"Second factor reset for {owner.email}; they set up a new one at the next sign-in.")
