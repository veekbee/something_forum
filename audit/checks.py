from django.core import checks
from django.db import connections


def database_roles(app_configs=None, databases=None, **kwargs):
    """manage.py check --deploy --database default: the audit table's role separation (rule 82)."""
    if not databases or "default" not in databases:
        return []
    from audit import roles

    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT current_user")
        app_role = cursor.fetchone()[0]
        problems = roles.check(cursor, app_role)
    return [checks.Error(problem, id="audit.E001") for problem in problems]
