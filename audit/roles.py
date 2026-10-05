"""The database roles that make the audit log append-only against the application itself
(docs/DESIGN.md, Privacy: Retention; rule 82).

- forum_audit owns the audit table and its trigger function. Nobody signs in as it.
- forum_retention is the only role the trigger lets DELETE audit rows. The retention job alone
  uses it, from the jobs service, with its own credentials.
- The application's role may only read and insert audit rows. It does not own the table, so it
  cannot drop the trigger, and it is a member of neither role.

setup() is run once by an administrator (manage.py setup_database_roles), and again after a
migration adds a table the retention job deletes from. check() lists anything that undoes the
separation; manage.py check --deploy --database default runs it.
"""

from psycopg import sql

OWNER_ROLE = "forum_audit"
RETENTION_ROLE = "forum_retention"
AUDIT_TABLE = "audit_auditentry"
TRIGGER_FUNCTION = "audit_reject_change()"


def _retention_tables():
    """(tables the retention job deletes from, tables it updates)."""
    from billing.models import Charge, Entitlement
    from boards.models import Attachment, Post, PostQuote, PostRevision, Thread, ThreadParticipant, ThreadRead
    from boards.models import ThreadTitleRevision
    from moderation.models import DMAccessGrant, ModerationAction, PermanentBanRecord, Report, SponsorReview

    models = (Report, SponsorReview, PermanentBanRecord, DMAccessGrant, ModerationAction,
              PostQuote, PostRevision, Attachment, Post, ThreadParticipant, ThreadTitleRevision, ThreadRead, Thread)
    deletes = [m._meta.db_table for m in models] + [AUDIT_TABLE]
    # Deleting a row deletes its many-to-many links too.
    deletes += [f.remote_field.through._meta.db_table for m in models for f in m._meta.local_many_to_many]
    updates = [ModerationAction._meta.db_table, Charge._meta.db_table, Entitlement._meta.db_table]
    return deletes, updates


def _role_exists(cursor, name):
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [name])
    return cursor.fetchone() is not None


def _is_superuser(cursor):
    cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    return cursor.fetchone()[0]


def setup(cursor, app_role, retention_password=None):
    """Create the roles if missing and apply ownership and grants. Idempotent."""
    I = sql.Identifier  # noqa: E741
    owner, retention, app = I(OWNER_ROLE), I(RETENTION_ROLE), I(app_role)
    if not _role_exists(cursor, OWNER_ROLE):
        cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(owner))
    if not _role_exists(cursor, RETENTION_ROLE):
        cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(retention))
    if retention_password:
        cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(retention, sql.Literal(retention_password)))
    superuser = _is_superuser(cursor)
    if not superuser:
        # Without superuser rights, handing the table to forum_audit needs membership of it. Only
        # development and test databases are set up this way; check() reports it.
        cursor.execute(sql.SQL("GRANT {}, {} TO CURRENT_USER").format(owner, retention))
    cursor.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(owner))
    cursor.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(I(AUDIT_TABLE), owner))
    cursor.execute(sql.SQL("ALTER FUNCTION audit_reject_change() OWNER TO {}").format(owner))

    cursor.execute(sql.SQL("REVOKE ALL ON {} FROM PUBLIC, {}").format(I(AUDIT_TABLE), app))
    cursor.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(I(AUDIT_TABLE), app))

    deletes, updates = _retention_tables()
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(retention))
    cursor.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(retention))
    cursor.execute(sql.SQL("GRANT INSERT ON {} TO {}").format(I(AUDIT_TABLE), retention))
    for table in deletes:
        cursor.execute(sql.SQL("GRANT DELETE ON {} TO {}").format(I(table), retention))
    for table in updates:
        cursor.execute(sql.SQL("GRANT UPDATE ON {} TO {}").format(I(table), retention))
    cursor.execute("SELECT pg_get_serial_sequence(%s, 'id')", [AUDIT_TABLE])
    sequence = cursor.fetchone()[0]
    if sequence:
        for role in (app, retention):
            cursor.execute(sql.SQL("GRANT USAGE ON SEQUENCE {} TO {}").format(sql.SQL(sequence), role))
    if superuser:
        # Tables added by later migrations stay readable to the retention job.
        cursor.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT ON TABLES TO {}")
                       .format(app, retention))


def check(cursor, app_role):
    """Problems that would let the application change or delete audit rows. Empty when sound."""
    problems = []
    for role in (OWNER_ROLE, RETENTION_ROLE):
        if not _role_exists(cursor, role):
            problems.append(f"The {role} role does not exist; run manage.py setup_database_roles.")
    if problems:
        return problems
    cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s", [app_role])
    row = cursor.fetchone()
    if row is None:
        return [f"The application role {app_role} does not exist."]
    if row[0]:
        problems.append(f"The application role {app_role} is a superuser.")
    for role in (OWNER_ROLE, RETENTION_ROLE):
        cursor.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", [app_role, role])
        if cursor.fetchone()[0]:
            problems.append(f"The application role {app_role} is a member of {role}.")
    cursor.execute("SELECT tableowner FROM pg_tables WHERE tablename = %s", [AUDIT_TABLE])
    if cursor.fetchone()[0] != OWNER_ROLE:
        problems.append(f"The audit table is not owned by {OWNER_ROLE}.")
    for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
        cursor.execute("SELECT has_table_privilege(%s, %s, %s)", [app_role, AUDIT_TABLE, privilege])
        if cursor.fetchone()[0]:
            problems.append(f"The application role {app_role} has {privilege} on the audit table.")
    cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal", [AUDIT_TABLE])
    if cursor.fetchone()[0] < 2:
        problems.append("The audit table's append-only triggers are missing.")
    return problems
