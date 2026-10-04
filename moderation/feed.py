"""The Mod feedback feed (docs/DESIGN.md, Staff views; rule 38): shared awareness among staff,
built from the records themselves rather than a store of its own.

It shows staff notes, escalations and how they were resolved, declined and withdrawn actions with
their reasons, and sponsor review decisions. Moderators see items about sub-forums they moderate or
about members; anything involving a DM is for Admins and Owners. Routine approvals and releases are
left out.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from accounts import roles
from moderation.models import ModerationAction, Report, SponsorReview

LIMIT = 100


@dataclass
class FeedItem:
    when: datetime
    kind: str
    text: str
    obj: Any


def _action_visible(actor, action):
    """An action is about a sub-forum if it is limited to one or tied to a post; otherwise it is
    about a member, which every staff member sees."""
    if roles.is_admin_or_owner(actor):
        return True
    post = action.related_post
    if post is not None and post.thread.kind == post.thread.Kind.DM:
        return False
    subforum = action.scope_subforum or (post.thread.subforum if post is not None else None)
    return subforum is None or roles.moderates(actor, subforum)


def _report_visible(actor, report):
    from core.permissions import can

    return bool(can(actor, "report.view", report))


def items(actor):
    found = []
    actions = ModerationAction.objects.filter(
        kind=ModerationAction.Kind.NOTE
    ) | ModerationAction.objects.filter(status__in=[ModerationAction.Status.DECLINED, ModerationAction.Status.WITHDRAWN])
    for action in actions.select_related("target_user", "initiated_by", "declined_by", "scope_subforum",
                                         "related_post__thread__subforum").order_by("-created_at")[:LIMIT]:
        if not _action_visible(actor, action):
            continue
        if action.kind == ModerationAction.Kind.NOTE:
            text = f"{action.initiated_by} noted on {action.target_user}: {action.internal_reason}"
            when = action.created_at
            kind = "note"
        elif action.status == ModerationAction.Status.DECLINED:
            text = (f"{action.declined_by} declined {action.initiated_by}'s {action.get_kind_display()} for "
                    f"{action.target_user}: {action.decline_reason}")
            when, kind = action.created_at, "declined"
        else:
            text = f"{action.initiated_by} withdrew a {action.get_kind_display()} for {action.target_user}"
            when, kind = action.created_at, "withdrawn"
        found.append(FeedItem(when, kind, text, action))

    for report in Report.objects.filter(escalated_by__isnull=False).select_related(
        "escalated_by", "handled_by", "post__thread__subforum", "user"
    ).order_by("-created_at")[:LIMIT]:
        if not _report_visible(actor, report):
            continue
        about = report.user or (report.post.author if report.post else "")
        text = f"{report.escalated_by} escalated a {report.get_kind_display()} about {about}: {report.escalation_note}"
        found.append(FeedItem(report.created_at, "escalated", text, report))
        if report.status == Report.Status.RESOLVED:
            found.append(FeedItem(report.handled_at, "resolved",
                                  f"{report.handled_by} resolved it: {report.get_outcome_display()}", report))

    for review in SponsorReview.objects.filter(status=SponsorReview.Status.DECIDED).select_related(
        "sponsor", "decided_by"
    ).order_by("-decided_at")[:LIMIT]:
        text = f"{review.decided_by} decided the sponsor review of {review.sponsor}: {review.get_outcome_display()}"
        found.append(FeedItem(review.decided_at, "sponsor_review", text, review))

    return sorted(found, key=lambda i: i.when, reverse=True)[:LIMIT]
