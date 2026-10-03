"""Make the audit log append-only at the database level (design rule 10)."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION audit_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditentry is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_auditentry_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_auditentry
    FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

CREATE TRIGGER audit_auditentry_no_truncate
    BEFORE TRUNCATE ON audit_auditentry
    FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_change();
"""

REVERSE = """
DROP TRIGGER audit_auditentry_no_truncate ON audit_auditentry;
DROP TRIGGER audit_auditentry_no_update_delete ON audit_auditentry;
DROP FUNCTION audit_reject_change();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
