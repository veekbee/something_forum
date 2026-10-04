"""Member reports and their handling (docs/DESIGN.md, Reports and the moderation queue; rules 33 to
35). Automatic flags share the table and the handling; see moderation.flags."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import roles
from audit import log
from boards.models import Post
from core.models import Notification
from core.services import require
from moderation.models import REASONS, Report


@transaction.atomic
def report(reporter, target, reason_key, note=""):
    """Report a post, a DM message or a member. Returns (report, created): reporting the same thing
    again while it waits adds nothing. The reported member never learns who reported them."""
    if reason_key not in REASONS:
        raise ValidationError("Choose a reason from the list.")
    require(reporter, "report.create", target)
    if isinstance(target, Post):
        kind = Report.Kind.DM if target.thread.kind == target.thread.Kind.DM else Report.Kind.POST
        subject = {"post": target}
    else:
        kind, subject = Report.Kind.MEMBER, {"user": target}
    existing = Report.objects.waiting().filter(reporter=reporter, **subject).first()
    if existing is not None:
        return existing, False
    if isinstance(target, Post):
        subject["user"] = target.author
    created = Report.objects.create(
        source=Report.Source.MEMBER, kind=kind, reporter=reporter, reason=reason_key, note=note.strip(), **subject
    )
    return created, True


def _locked(item):
    return Report.objects.select_for_update(of=("self",)).select_related("post__thread__subforum").get(pk=item.pk)


@transaction.atomic
def resolve(actor, item, outcome, hide_reason="", hide_note=""):
    """Close a report or flag. With hide_reason, the reported post is hidden too (rule 35) and the
    outcome is "action taken". The reporter hears the outcome in one line; an escalating Moderator
    hears it as well."""
    from boards import services

    item = _locked(item)
    require(actor, "report.resolve", item)
    if hide_reason:
        if item.post is None:
            raise ValidationError("There is no post to hide.")
        if item.post.deleted_at is None:
            services.delete_post(actor, item.post, hide_reason, hide_note)
        outcome = Report.Outcome.ACTION_TAKEN
    if outcome not in Report.Outcome.values:
        raise ValidationError("Choose an outcome.")
    item.status, item.outcome = Report.Status.RESOLVED, outcome
    item.handled_by, item.handled_at = actor, timezone.now()
    item.save(update_fields=["status", "outcome", "handled_by", "handled_at"])
    log.record(actor, "report.resolve", item, {"outcome": outcome, "kind": item.kind})
    if item.reporter_id and item.kind != Report.Kind.ESCALATION:
        Notification.objects.create(recipient_id=item.reporter_id, kind="report.outcome",
                                    payload={"report": item.pk, "outcome": outcome})
    if item.escalated_by_id and item.escalated_by_id != actor.pk:
        Notification.objects.create(recipient_id=item.escalated_by_id, kind="queue.escalation_resolved",
                                    payload={"report": item.pk, "outcome": outcome})
    return item


@transaction.atomic
def escalate(actor, item, note):
    """A Moderator hands an item to Admins and Owners. It stays visible to Moderators, marked."""
    item = _locked(item)
    require(actor, "report.escalate", item)
    if not note.strip():
        raise ValidationError("Say why you are escalating.")
    item.status, item.escalated_by, item.escalation_note = Report.Status.ESCALATED, actor, note.strip()
    item.save(update_fields=["status", "escalated_by", "escalation_note"])
    log.record(actor, "report.escalate", item, {"kind": item.kind})
    for user in roles.leadership():
        Notification.objects.create(recipient=user, kind="queue.escalated", payload={"report": item.pk})
    return item


@transaction.atomic
def escalate_item(actor, item, note):
    """Escalate a held post, a pending action or a promotion: an escalation report points at it, and
    until an Admin or Owner resolves that report, only they act on the item. Resolving the report
    does not by itself release, approve or decide the item."""
    from boards.models import Post
    from moderation.models import ModerationAction

    require(actor, "queue.escalate", item)
    if not note.strip():
        raise ValidationError("Say why you are escalating.")
    if isinstance(item, Post):
        link, about = {"post": item}, item.author
    elif isinstance(item, ModerationAction):
        link, about = {"related_action": item}, item.target_user
    else:
        link, about = {"related_promotion": item}, item.member
    escalation = Report.objects.create(
        source=Report.Source.MEMBER, kind=Report.Kind.ESCALATION, reporter=actor, user=about,
        status=Report.Status.ESCALATED, escalated_by=actor, escalation_note=note.strip(), **link,
    )
    log.record(actor, "report.escalate", escalation, {"kind": escalation.kind})
    for user in roles.leadership():
        Notification.objects.create(recipient=user, kind="queue.escalated", payload={"report": escalation.pk})
    return escalation


@transaction.atomic
def add_note(actor, item, note):
    item = _locked(item)
    require(actor, "report.note", item)
    if not note.strip():
        raise ValidationError("Write a note.")
    item.details.setdefault("notes", []).append(
        {"by": actor.pk, "name": actor.display_name, "at": timezone.now().isoformat(), "note": note.strip()}
    )
    item.save(update_fields=["details"])
    log.record(actor, "report.note", item)
    return item
