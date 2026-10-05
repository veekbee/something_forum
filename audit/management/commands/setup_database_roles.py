import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from audit import roles


class Command(BaseCommand):
    help = ("Create the forum_audit and forum_retention roles and hand them the audit table (rule 82). "
            "Run once by an administrator, with DATABASE_URL pointing at an administrator account, "
            "and again after a migration adds a table the retention job deletes from. The retention "
            "role's password comes from RETENTION_ROLE_PASSWORD. --check only reports problems.")

    def add_arguments(self, parser):
        parser.add_argument("--app-role", required=True, help="The role the application signs in as.")
        parser.add_argument("--check", action="store_true", help="Report problems without changing anything.")

    def handle(self, *args, app_role, check=False, **options):
        with connection.cursor() as cursor:
            if not check:
                roles.setup(cursor, app_role, os.environ.get("RETENTION_ROLE_PASSWORD") or None)
            problems = roles.check(cursor, app_role)
        for problem in problems:
            self.stdout.write(problem)
        if problems and check:
            raise CommandError(f"{len(problems)} problem(s) with the database roles.")
        if not problems:
            self.stdout.write("The database roles are sound.")
