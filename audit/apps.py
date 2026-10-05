from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "audit"

    def ready(self):
        from django.core import checks

        from audit import checks as audit_checks

        checks.register(audit_checks.database_roles, checks.Tags.database, deploy=True)
