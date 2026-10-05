"""Build step 9: audit retention and its database roles (rule 82, Privacy: Retention)."""

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from accounts.models import User
from audit import log, retention, roles
from audit.models import AuditEntry
from boards.models import Post, PostRevision, Thread, ThreadParticipant
from moderation import permanent
from moderation import reports as reporting
from moderation import services as moderation
from moderation.models import ModerationAction, PermanentBanRecord, Report
from tests.factories import make_dm, make_post, make_thread

pytestmark = [pytest.mark.usefixtures("database_roles"), pytest.mark.django_db]

REASON = "spam"
LONG = timedelta(days=3 * 365)   # left longer ago than the two-year period
RECENT = timedelta(days=365)     # left within it


def left(user, ago, status=User.Status.REMOVED):
    User.objects.filter(pk=user.pk).update(status=status, removed_at=timezone.now() - ago)


def entry(actor, action, target, payload=None):
    with transaction.atomic():
        return log.record(actor, action, target, payload)


def exists(e):
    return AuditEntry.objects.filter(pk=e.pk).exists()


# --- the roles ---------------------------------------------------------------------------------


def as_role(role, statement, params=()):
    """Run one statement as `role` inside a savepoint; True if the database allowed it."""
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {role}")
            cursor.execute(statement, params)
            raise _Allowed
    except _Allowed:
        return True
    except DatabaseError:
        return False


class _Allowed(Exception):
    pass


@pytest.mark.parametrize("statement", [
    "UPDATE audit_auditentry SET action = 'x'",
    "DELETE FROM audit_auditentry",
    "TRUNCATE audit_auditentry",
    "DROP TRIGGER audit_auditentry_no_update_delete ON audit_auditentry",
    "ALTER TABLE audit_auditentry DISABLE TRIGGER ALL",
    "CREATE OR REPLACE FUNCTION audit_reject_change() RETURNS trigger AS $$ BEGIN RETURN OLD; END; $$ LANGUAGE plpgsql",
])
def test_the_application_role_cannot_change_the_audit_log(seeded, database_roles, statement):
    entry(None, "test.entry", User.objects.first())
    assert not as_role(database_roles, statement)


def test_the_application_role_reads_and_appends(seeded, database_roles):
    assert as_role(database_roles, "SELECT count(*) FROM audit_auditentry")
    assert as_role(database_roles, "INSERT INTO audit_auditentry (action, target_type, target_id, payload, created_at) "
                                   "VALUES ('x', '', '', '{}', now())")


@pytest.mark.parametrize("statement,allowed", [
    ("DELETE FROM audit_auditentry", True),
    ("UPDATE audit_auditentry SET action = 'x'", False),
    ("TRUNCATE audit_auditentry", False),
    ("DROP TRIGGER audit_auditentry_no_update_delete ON audit_auditentry", False),
])
def test_the_retention_role_can_only_delete(seeded, statement, allowed):
    entry(None, "test.entry", User.objects.first())
    assert as_role(roles.RETENTION_ROLE, statement) is allowed


def test_no_other_role_deletes_even_the_owner(seeded):
    entry(None, "test.entry", User.objects.first())
    assert not as_role(roles.OWNER_ROLE, "DELETE FROM audit_auditentry")


def test_check_reports_a_sound_setup_and_an_unsound_one(database_roles):
    with connection.cursor() as cursor:
        assert roles.check(cursor, database_roles) == []
        cursor.execute("SELECT current_user")
        test_role = cursor.fetchone()[0]
        problems = roles.check(cursor, test_role)  # the test role is a member, as in development
    assert any("member of forum_audit" in p for p in problems)


def test_the_deploy_check(database_roles):
    from audit.checks import database_roles as check

    errors = check(databases=["default"])
    assert errors and all(e.id == "audit.E001" for e in errors)
    assert check(databases=None) == []


def test_setup_command_reports(database_roles):
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    out = StringIO()
    call_command("setup_database_roles", "--app-role", database_roles, "--check", stdout=out)
    assert "sound" in out.getvalue()
    with pytest.raises(CommandError):
        call_command("setup_database_roles", "--app-role", "forum", "--check", stdout=StringIO())


def test_the_job_works_as_the_retention_role(seeded, monkeypatch):
    seen = []
    original = retention.due_audit_entries

    def spy(using, *args):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            seen.append(cursor.fetchone()[0])
        return original(using, *args)

    monkeypatch.setattr(retention, "due_audit_entries", spy)
    retention.run()
    assert seen == [roles.RETENTION_ROLE]
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        assert cursor.fetchone()[0] != roles.RETENTION_ROLE  # and hands the role back afterwards


# --- audit entries ------------------------------------------------------------------------------


def test_entries_go_once_every_member_they_name_left_long_enough_ago(make_user, general):
    member, staff, other = make_user("full"), make_user("admin"), make_user("full")
    own = entry(member, "member.leave", member)
    by_staff = entry(staff, "role.grant", member)
    about_post = entry(None, "post.release", make_post(make_thread(general, member), member))
    by_payload = entry(None, "billing.refund", User.objects.get(pk=other.pk), {"member": member.pk})
    left(member, LONG)
    retention.run()
    assert not exists(own) and not exists(about_post)
    assert exists(by_staff)       # the Admin who acted is still a member
    assert exists(by_payload)     # names `other` as target too
    left(staff, LONG)
    left(other, LONG)
    retention.run()
    assert not exists(by_staff) and not exists(by_payload)


def test_recent_departures_and_reinstated_members_are_kept(make_user):
    recent, reinstated = make_user("full"), make_user("full")
    a, b = entry(recent, "member.leave", recent), entry(reinstated, "member.leave", reinstated)
    left(recent, RECENT)
    User.objects.filter(pk=reinstated.pk).update(status=User.Status.ACTIVE, removed_at=None)
    retention.run()
    assert exists(a) and exists(b)


def test_the_period_is_the_setting(make_user, owner):
    from core.models import SiteSetting

    member = make_user("full")
    e = entry(member, "member.leave", member)
    left(member, timedelta(days=400))
    SiteSetting.objects.create(key="retention.audit_years_after_departure", value=1, updated_by=owner)
    retention.run()
    assert not exists(e)


def test_entries_about_the_forum_are_kept_for_good(make_user, owner):
    from core.models import SiteSetting

    row = SiteSetting.objects.create(key="session.retention_days", value=100, updated_by=owner)
    write = entry(owner, "site_setting.write", row, {"key": row.key})
    reset = entry(owner, "site_setting.reset", owner, {"key": row.key})
    left(owner, LONG)
    retention.run()
    assert exists(write) and exists(reset)


def test_entries_about_a_sub_forum_are_kept_whoever_acted(make_user, general):
    admin = make_user("admin")
    e = entry(admin, "subforum.edit", general)
    left(admin, LONG)
    retention.run()
    assert exists(e)


def test_a_vanished_target_naming_nobody_goes_by_its_own_age(make_user, monkeypatch):
    member = make_user("full")
    gone = entry(None, "post.release", member)
    # Pretend the target row vanished (a deleted conversation, a deleted session record).
    monkeypatch.setattr(retention, "_resolve_targets", lambda using, wanted: {k: {} for k in wanted})
    retention.run()
    assert exists(gone)
    retention.run(now=timezone.now() + timedelta(days=800))
    assert not exists(gone)


def test_ended_invitations_count_as_leaving(make_user, owner):
    from sponsorship import onboarding
    from tests.factories import accepted

    from sponsorship.models import Invitation

    invitation = accepted(make_user("full"))
    invitee = invitation.invitee
    mine = entry(invitee, "test.invitee_acted", invitee)
    onboarding.decline(owner, invitation, "Not this time")
    assert User.objects.get(pk=invitee.pk).removed_at is None  # never a membership, so no removal date
    retention.run()
    assert exists(mine)
    Invitation.objects.filter(pk=invitation.pk).update(decided_at=timezone.now() - LONG)
    retention.run()
    assert not exists(mine)


def test_deleted_accounts_count_as_left_when_they_were_deleted(make_user):
    gone_id = 999_999
    named = entry(None, "billing.refund", User.objects.filter(pk=make_user("full").pk).get(), {"member": gone_id})
    left(User.objects.get(pk=named.target_id), LONG)
    retention.run()
    assert not exists(named)  # no record of the deletion left: it was longer ago than any cutoff


def test_a_recently_deleted_account_counts_from_its_deletion(make_user, owner):
    from sponsorship.models import Invitation
    from tests.factories import accepted

    invitation = accepted(make_user("full"))
    gone_id = invitation.invitee_id
    named = entry(None, "billing.refund", invitation.sponsor, {"member": gone_id})
    entry(None, "invitation.account_deleted", invitation, {"invitee": gone_id})
    Invitation.objects.filter(pk=invitation.pk).update(invitee=None)
    from core.models import Notification
    from accounts.models import IdentityRecord

    IdentityRecord.objects.filter(user_id=gone_id).delete()
    Notification.objects.filter(recipient_id=gone_id).delete()
    User.objects.filter(pk=gone_id).delete()
    left(invitation.sponsor, LONG)
    retention.run()
    assert exists(named)  # deleted today, so not yet two years gone


# --- moderation records --------------------------------------------------------------------------


def _warn(staff, member):
    action = moderation.initiate_action(staff, member, "warning", internal_reason="Rude", public_summary="Rudeness")
    return action


def test_moderation_records_go_by_the_same_rule(make_user, general):
    admin, member, reporter = make_user("admin"), make_user("full"), make_user("full")
    post = make_post(make_thread(general, reporter), member)
    report, _ = reporting.report(reporter, post, REASON)
    action = _warn(admin, member)
    left(member, LONG)
    left(reporter, LONG)
    retention.run()
    assert Report.objects.filter(pk=report.pk).exists() is False
    assert ModerationAction.objects.filter(pk=action.pk).exists()  # the Admin is still here
    left(admin, LONG)
    counts = retention.run()
    assert not ModerationAction.objects.filter(pk=action.pk).exists()
    assert counts["moderation_records"] >= 1


def test_a_standing_permanent_ban_keeps_its_record_and_action(make_user, owner):
    member = make_user("full")
    action = permanent.impose(owner, member, "repeated harassment", "Harassment")
    left(member, LONG)
    left(owner, LONG)
    retention.run()
    assert PermanentBanRecord.objects.filter(action=action).exists()
    assert ModerationAction.objects.filter(pk=action.pk).exists()
    PermanentBanRecord.objects.filter(action=action).update(annulled_at=timezone.now() - LONG)
    retention.run()
    assert not ModerationAction.objects.filter(pk=action.pk).exists()


def test_payments_stay_and_lose_only_their_link(make_user, owner):
    from billing.models import Charge

    member, admin = make_user("full"), make_user("admin")
    action = _warn(admin, member)
    charge = Charge.objects.create(user=member, kind=Charge.Kind.BAN_REVERSAL, amount_cents=500,
                                   stripe_payment_intent_id="pi_test_retention", related_action=action)
    left(member, LONG)
    left(admin, LONG)
    retention.run()
    charge.refresh_from_db()
    assert charge.related_action is None and charge.amount_cents == 500


def test_an_action_a_kept_record_refers_to_is_kept(make_user):
    admin, member, staying = make_user("admin"), make_user("full"), make_user("full")
    action = _warn(admin, member)
    flag = Report.objects.create(kind=Report.Kind.FLAG_RATE_LIMIT, source="system", user=staying, related_action=action)
    left(member, LONG)
    left(admin, LONG)
    retention.run()
    assert Report.objects.filter(pk=flag.pk).exists()
    assert ModerationAction.objects.filter(pk=action.pk).exists()


# --- conversations ---------------------------------------------------------------------------------


def test_conversations_go_once_everyone_left_long_enough_ago(make_user):
    a, b, c = make_user("full"), make_user("full"), make_user("full")
    gone, recent, staying = make_dm(a, b), make_dm(a, c), make_dm(b, c)
    for thread in (gone, recent, staying):
        post = make_post(thread, thread.author)
        PostRevision.objects.create(post=post, body_source="old", edited_by=thread.author)
    left(a, LONG)
    left(b, LONG)
    left(c, RECENT)
    counts = retention.run()
    assert not Thread.objects.filter(pk=gone.pk).exists()
    assert not Post.objects.filter(thread_id=gone.pk).exists()
    assert not ThreadParticipant.objects.filter(thread_id=gone.pk).exists()
    assert Thread.objects.filter(pk=recent.pk).exists() and Thread.objects.filter(pk=staying.pk).exists()
    assert counts["conversations"] == 1


def test_a_conversation_a_kept_record_refers_to_is_kept(make_user):
    a, b, admin = make_user("full"), make_user("full"), make_user("admin")
    thread = make_dm(a, b)
    post = make_post(thread, a)
    moderation.initiate_action(admin, a, "warning", internal_reason="In a DM", related_post=post)
    left(a, LONG)
    left(b, LONG)
    retention.run()
    assert Thread.objects.filter(pk=thread.pk).exists()


def test_discussion_threads_are_never_deleted(make_user, general):
    a = make_user("full")
    thread = make_thread(general, a)
    make_post(thread, a)
    left(a, LONG)
    retention.run()
    assert Thread.objects.filter(pk=thread.pk).exists()


# --- the run ------------------------------------------------------------------------------------


def test_each_run_writes_one_entry_with_counts_and_no_ids(make_user):
    member = make_user("full")
    entry(member, "member.leave", member)
    left(member, LONG)
    counts = retention.run()
    run = AuditEntry.objects.get(action="retention.run")
    assert run.payload == counts and counts["audit_entries"] == 1
    assert run.actor is None and run.target_type == "" and run.target_id == ""
    retention.run()
    assert AuditEntry.objects.filter(action="retention.run").count() == 2  # kept for good


def test_a_failed_run_deletes_nothing(make_user, monkeypatch):
    member = make_user("full")
    e = entry(member, "member.leave", member)
    left(member, LONG)

    def broken(*args, **kwargs):
        raise RuntimeError("no entry")

    monkeypatch.setattr(retention.log, "record", broken)
    with pytest.raises(RuntimeError):
        retention.run()
    assert exists(e)
