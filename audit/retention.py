"""The retention job (docs/DESIGN.md, Privacy: Retention; rules 82 and the DM position).

An audit entry, a moderation record or a direct-message conversation is deleted once every member
it refers to left more than retention.audit_years_after_departure ago. Leaving means removed,
erased or an invitation that ended; a reinstated member has not left. Everything runs in one
transaction as the forum_retention role, with one audit entry for the run (cutoff and counts).

Which members an audit entry refers to:
- its actor, and the members behind its target (a post's author, a report's reporter and reported
  member, a conversation's participants: TARGET_MEMBERS), and the ids under PAYLOAD_MEMBER_KEYS;
- entries about the forum itself (KEPT_ACTIONS, and targets that are not about a member, such as
  sub-forums) are kept for good, whoever acted;
- an entry whose target row no longer exists and that names no other member is due once it is
  itself older than the cutoff.
"""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone
from psycopg import sql

from audit import log
from audit.roles import RETENTION_ROLE

KEPT_ACTIONS = {"site_setting.write", "site_setting.reset", "billing.launch", "retention.run"}

PAYLOAD_MEMBER_KEYS = {"author", "invitee", "member", "moderator", "new_sponsor", "recipient", "sponsor",
                       "subjects", "target_user", "user"}

# Target model label -> the lookups that lead from a target row to the members it is about.
TARGET_MEMBERS = {
    "accounts.user": ("pk",),
    "accounts.roleassignment": ("user_id",),
    "accounts.usersession": ("user_id",),
    "accounts.block": ("blocker_id", "blocked_id"),
    "accounts.identityrecord": ("user_id",),
    "billing.subscription": ("user_id",),
    "billing.entitlement": ("user_id",),
    "billing.charge": ("user_id",),
    "billing.gift": ("sponsor_id", "invitee_id"),
    "billing.lapseperiod": ("user_id",),
    "boards.thread": ("author_id", "participants__user_id"),
    "boards.post": ("author_id",),
    "boards.customemoji": ("purchaser_id",),
    "core.datarequest": ("user_id",),
    "moderation.report": ("reporter_id", "user_id"),
    "moderation.moderationaction": ("target_user_id",),
    "moderation.sponsorreview": ("sponsor_id", "banned_member_id"),
    "moderation.dmaccessgrant": ("moderator_id", "subject_users"),
    "moderation.permanentbanrecord": ("action__target_user_id",),
    "sponsorship.invitation": ("sponsor_id", "invitee_id"),
    "sponsorship.sponsorship": ("sponsor_id", "member_id"),
    "sponsorship.sponsorshiptransfer": ("member_id",),
    "sponsorship.sponsorshipoffer": ("offerer_id", "transfer__member_id"),
    "sponsorship.vouchrequest": ("recipient_id", "transfer__member_id"),
    "sponsorship.promotion": ("member_id",),
}

LONG_AGO = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)


def alias():
    return "retention" if "retention" in settings.DATABASES else "default"


def cutoff_for(now, years):
    try:
        return now.replace(year=now.year - years)
    except ValueError:  # 29 February
        return now.replace(year=now.year - years, day=28)


class Departures:
    """When each member left, or None for members who have not."""

    def __init__(self, using):
        from accounts.models import User
        from audit.models import AuditEntry
        from sponsorship.models import Invitation

        self.existing = set(User.objects.using(using).values_list("pk", flat=True))
        self.left = {}
        undated = []
        for pk, removed_at in User.objects.using(using).filter(
                status__in=[User.Status.REMOVED, User.Status.TOMBSTONE]).values_list("pk", "removed_at"):
            if removed_at:
                self.left[pk] = removed_at
            else:
                undated.append(pk)  # an invited account whose invitation ended
        for pk, decided_at in Invitation.objects.using(using).filter(invitee_id__in=undated).values_list(
                "invitee_id", "decided_at"):
            if decided_at:
                self.left[pk] = decided_at
        # Accounts deleted after their invitation ended (rule 19); if that entry is itself gone,
        # the deletion was older than any cutoff.
        self.deleted = {}
        for payload, created_at in AuditEntry.objects.using(using).filter(
                action="invitation.account_deleted").values_list("payload", "created_at"):
            if isinstance(payload.get("invitee"), int):
                self.deleted[payload["invitee"]] = created_at

    def left_before(self, member, cutoff):
        if member not in self.existing:
            return self.deleted.get(member, LONG_AGO) < cutoff
        when = self.left.get(member)
        return when is not None and when < cutoff

    def all_left_before(self, members, cutoff):
        return bool(members) and all(self.left_before(m, cutoff) for m in members)


def _ids(value):
    if isinstance(value, bool):
        return set()
    if isinstance(value, int):
        return {value}
    if isinstance(value, list):
        return {v for v in value if isinstance(v, int) and not isinstance(v, bool)}
    return set()


def _resolve_targets(using, wanted):
    """{label: {target_id: members}} for the target ids wanted per label."""
    from django.apps import apps

    resolved = {}
    for label, ids in wanted.items():
        lookups = TARGET_MEMBERS[label]
        model = apps.get_model(label)
        found = resolved.setdefault(label, {})
        numeric = [int(i) for i in ids if i.isdigit()]
        for row in model.objects.using(using).filter(pk__in=numeric).values_list("pk", *lookups):
            found.setdefault(str(row[0]), set()).update(v for v in row[1:] if v)
    return resolved


def due_audit_entries(using, departures, cutoff):
    from audit.models import AuditEntry

    rows = list(AuditEntry.objects.using(using).exclude(action__in=KEPT_ACTIONS).values_list(
        "pk", "actor_id", "target_type", "target_id", "payload", "created_at"))
    wanted = {}
    for _, _, label, target_id, _, _ in rows:
        if label in TARGET_MEMBERS:
            wanted.setdefault(label, set()).add(target_id)
    targets = _resolve_targets(using, wanted)
    due = []
    for pk, actor, label, target_id, payload, created_at in rows:
        members = {actor} if actor else set()
        for key in PAYLOAD_MEMBER_KEYS & set(payload or {}):
            members |= _ids(payload[key])
        if label not in TARGET_MEMBERS:
            continue  # about the forum rather than a member: kept for good, whoever acted
        target_members = targets[label].get(target_id)
        if target_members is not None:
            members |= target_members
        elif not members:
            if created_at < cutoff:  # the target is gone and names nobody else
                due.append(pk)
            continue
        if departures.all_left_before(members, cutoff):
            due.append(pk)
    return due


def _due(subjects, departures, cutoff):
    return {pk for pk, ids in subjects.items() if departures.all_left_before(ids, cutoff)}


def _subjects(using):
    """Who each moderation record is about, leaving out the staff who handled it (decided 5 Oct
    2026): an action's target; a report's reporter and reported member (the post's author if no
    member is named), except that an escalation's reporter is the escalating Moderator; a sponsor
    review's sponsor and banned member; the members a DM access grant covers."""
    from moderation.models import DMAccessGrant, ModerationAction, Report, SponsorReview

    actions = {pk: {t} for pk, t in ModerationAction.objects.using(using).values_list("pk", "target_user_id")}
    reports = {}
    for pk, kind, reporter, user, author in Report.objects.using(using).values_list(
            "pk", "kind", "reporter_id", "user_id", "post__author_id"):
        ids = {user or author}
        if kind != Report.Kind.ESCALATION:
            ids.add(reporter)
        reports[pk] = {i for i in ids if i}
    reviews = {pk: {a for a in (sponsor, banned) if a} for pk, sponsor, banned in
               SponsorReview.objects.using(using).values_list("pk", "sponsor_id", "banned_member_id")}
    grants = {pk: set() for pk in DMAccessGrant.objects.using(using).values_list("pk", flat=True)}
    for pk, user in DMAccessGrant.subject_users.through.objects.using(using).values_list(
            "dmaccessgrant_id", "user_id"):
        grants[pk].add(user)
    return actions, reports, reviews, grants


def due_moderation_records(using, departures, cutoff):
    """Reports (flags and escalations included), sponsor reviews, DM access grants, annulled
    permanent-ban records and moderation actions, keeping any action something kept refers to."""
    from moderation.models import ModerationAction, PermanentBanRecord, Report, SponsorReview

    subjects = _subjects(using)
    actions, reports, reviews, grants = (_due(s, departures, cutoff) for s in subjects)
    bans = {}
    for pk, action_id, annulled_at in PermanentBanRecord.objects.using(using).values_list(
            "pk", "action_id", "annulled_at"):
        bans[pk] = (action_id, annulled_at)
    ban_records = {pk for pk, (action_id, annulled_at) in bans.items() if annulled_at and action_id in actions}

    refs = []  # (referring row is kept?, action it refers to)
    for pk, action_id in Report.objects.using(using).filter(related_action__isnull=False).values_list(
            "pk", "related_action_id"):
        refs.append((("report", pk), action_id))
    for pk, a, b in SponsorReview.objects.using(using).values_list("pk", "triggering_action_id", "resulting_action_id"):
        refs += [(("review", pk), a), (("review", pk), b)]
    for pk, (action_id, _) in bans.items():
        refs.append((("ban", pk), action_id))
    for pk, action_id in ModerationAction.objects.using(using).filter(related_action__isnull=False).values_list(
            "pk", "related_action_id"):
        refs.append((("action", pk), action_id))
    sets = {"report": reports, "review": reviews, "ban": ban_records, "action": actions}
    changed = True
    while changed:
        changed = False
        for (kind, pk), action_id in refs:
            if action_id in actions and pk not in sets[kind]:
                actions.discard(action_id)
                changed = True
        ban_records = {pk for pk in ban_records if bans[pk][0] in actions}
        sets["ban"] = ban_records
    return {"reports": reports, "reviews": reviews, "grants": grants, "bans": ban_records, "actions": actions}


def due_conversations(using, departures, cutoff, keep_posts):
    """DM conversations whose participants have all left, the last more than the period ago, with
    no message a kept moderation record refers to."""
    from boards.models import Post, Thread, ThreadParticipant

    members = {}
    for thread_id, user_id in ThreadParticipant.objects.using(using).filter(
            thread__kind=Thread.Kind.DM).values_list("thread_id", "user_id"):
        members.setdefault(thread_id, set()).add(user_id)
    due = {t for t, ids in members.items() if departures.all_left_before(ids, cutoff)}
    held = set(Post.objects.using(using).filter(pk__in=keep_posts, thread_id__in=due).values_list("thread_id", flat=True))
    return due - held


def _delete_conversations(using, threads):
    from boards.models import (
        Attachment, Post, PostQuote, PostRevision, Thread, ThreadParticipant, ThreadRead, ThreadTitleRevision,
    )

    posts = Post.objects.using(using).filter(thread_id__in=threads).values("pk")
    PostQuote.objects.using(using).filter(Q(quoting_post__in=posts) | Q(quoted_post__in=posts)).delete()
    PostRevision.objects.using(using).filter(post__in=posts).delete()
    files = list(Attachment.objects.using(using).filter(post__in=posts).values_list("storage_key", flat=True))
    Attachment.objects.using(using).filter(post__in=posts).delete()
    # Posts refuse delete() (soft deletion only, rule 11); retention is the deliberate exception.
    with connections[using].cursor() as cursor:
        cursor.execute(f"DELETE FROM {Post._meta.db_table} WHERE thread_id = ANY(%s)", [list(threads)])
    ThreadParticipant.objects.using(using).filter(thread_id__in=threads).delete()
    ThreadTitleRevision.objects.using(using).filter(thread_id__in=threads).delete()
    ThreadRead.objects.using(using).filter(thread_id__in=threads).delete()
    Thread.objects.using(using).filter(pk__in=threads).delete()

    def remove_files():
        from django.core.files.storage import default_storage

        for key in files:
            if key:
                default_storage.delete(key)

    transaction.on_commit(remove_files, using=using)


def _delete_moderation_records(using, due):
    from billing.models import Charge, Entitlement
    from moderation.models import DMAccessGrant, ModerationAction, PermanentBanRecord, Report, SponsorReview

    Report.objects.using(using).filter(pk__in=due["reports"]).delete()
    PermanentBanRecord.objects.using(using).filter(pk__in=due["bans"]).delete()
    SponsorReview.objects.using(using).filter(pk__in=due["reviews"]).delete()
    for grant in DMAccessGrant.objects.using(using).filter(pk__in=due["grants"]):
        grant.subject_users.clear()
    DMAccessGrant.objects.using(using).filter(pk__in=due["grants"]).delete()
    # Payments stay for accounting; only their link to the deleted action goes.
    Charge.objects.using(using).filter(related_action_id__in=due["actions"]).update(related_action=None)
    Entitlement.objects.using(using).filter(revoked_by_action_id__in=due["actions"]).update(revoked_by_action=None)
    ModerationAction.objects.using(using).filter(pk__in=due["actions"]).update(related_action=None)
    ModerationAction.objects.using(using).filter(pk__in=due["actions"]).delete()


def run(now=None):
    """Delete what is due. Returns the counts written to the run's audit entry."""
    from core import registry
    from moderation.models import ModerationAction, Report

    now = now or timezone.now()
    cutoff = cutoff_for(now, registry.site_value("retention.audit_years_after_departure"))
    using = alias()
    connection = connections[using]
    with transaction.atomic(using=using):
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(RETENTION_ROLE)))
        departures = Departures(using)
        audit_due = due_audit_entries(using, departures, cutoff)
        records = due_moderation_records(using, departures, cutoff)
        kept_posts = set(Report.objects.using(using).exclude(pk__in=records["reports"]).filter(
            post__isnull=False).values_list("post_id", flat=True))
        kept_posts |= set(ModerationAction.objects.using(using).exclude(pk__in=records["actions"]).filter(
            related_post__isnull=False).values_list("related_post_id", flat=True))
        conversations = due_conversations(using, departures, cutoff, kept_posts)

        _delete_moderation_records(using, records)
        _delete_conversations(using, conversations)
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_auditentry WHERE id = ANY(%s)", [audit_due])
        counts = {
            "cutoff": cutoff.isoformat(),
            "audit_entries": len(audit_due),
            "moderation_records": sum(len(v) for v in records.values()),
            "conversations": len(conversations),
        }
        log.record(None, "retention.run", None, counts, using=using)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE NONE")
    return counts
