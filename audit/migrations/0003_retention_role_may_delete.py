"""Let only the retention role delete audit entries (rule 82). UPDATE and TRUNCATE stay refused
for everyone, and every other role is still refused DELETE. The roles themselves are created by
manage.py setup_database_roles (audit/roles.py)."""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION audit_reject_change() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_user = 'forum_retention' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_auditentry is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""

REVERSE = """
CREATE OR REPLACE FUNCTION audit_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditentry is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0002_append_only_trigger")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
