"""The moderation queue (docs/DESIGN.md, The moderation queue): one list of everything waiting on
staff, oldest first, each item visible to exactly the staff the queue table names."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from boards.models import Post, Thread
from core.permissions import can
from moderation.models import ModerationAction, Report, SponsorReview
from sponsorship.models import Promotion

TYPES = {
    "held": "Held posts",
    "report": "Reports",
    "flag": "Automatic flags",
    "action": "Actions awaiting approval",
    "promotion": "Promotions",
    "sponsor_review": "Sponsor reviews",
    "transfer": "Sponsorship transfers past their deadline",
}


@dataclass
class Item:
    type: str
    obj: Any
    waiting_since: datetime


def _visible(actor, action, item, link):
    """Staff who may act on an item see it; so do staff who can see its open escalation, since an
    escalated item stays in the queue, marked, while it waits for an Admin or Owner."""
    if can(actor, action, item):
        return True
    escalation = Report.objects.waiting().filter(kind=Report.Kind.ESCALATION, **{link: item}).first()
    return escalation is not None and bool(can(actor, "report.view", escalation))


def _held(actor):
    posts = Post.objects.filter(is_held=True, thread__kind=Thread.Kind.DISCUSSION).select_related(
        "author", "thread__subforum"
    )
    return [Item("held", p, p.created_at) for p in posts if _visible(actor, "post.moderate", p, "post")]


def _reports(actor):
    found = []
    for report in Report.objects.waiting().select_related("post__thread__subforum", "post__author", "user", "reporter",
                                                        "escalated_by"):
        if can(actor, "report.view", report):
            found.append(Item("flag" if report.is_flag else "report", report, report.created_at))
    return found


def _actions(actor):
    pending = ModerationAction.objects.filter(status=ModerationAction.Status.PENDING).select_related(
        "target_user", "initiated_by", "related_post__thread__subforum"
    )
    return [Item("action", a, a.created_at) for a in pending
            if _visible(actor, "moderation.approve", a, "related_action")]


def _promotions(actor):
    open_ = Promotion.objects.open().select_related("member", "recommended_by", "reviewed_by", "from_role", "to_role")
    found = []
    for promotion in open_:
        action = "promotion.review" if promotion.status == Promotion.Status.RECOMMENDED else "promotion.decide"
        if _visible(actor, action, promotion, "related_promotion"):
            found.append(Item("promotion", promotion, promotion.recommended_at or promotion.created_at))
    return found


def _sponsor_reviews(actor):
    pending = SponsorReview.objects.filter(status=SponsorReview.Status.PENDING).select_related("sponsor", "banned_member")
    return [Item("sponsor_review", r, r.created_at) for r in pending if can(actor, "sponsor_review.decide", r)]


def _transfers(actor):
    """Rule 53: a transfer past its deadline waits for an Admin or Owner; nothing is automatic."""
    from sponsorship.transfers import past_deadline

    due = past_deadline().select_related("member", "sponsorship__sponsor")
    return [Item("transfer", t, t.deadline_at) for t in due if can(actor, "sponsorship.decide_transfer", t)]


def items(actor, only=None):
    if not can(actor, "queue.view"):
        return []
    sources = {"held": _held, "report": _reports, "flag": _reports, "action": _actions,
               "promotion": _promotions, "sponsor_review": _sponsor_reviews, "transfer": _transfers}
    collected, seen = [], set()
    for type_, source in sources.items():
        if only and type_ != only or source in seen:
            continue
        seen.add(source)
        collected.extend(source(actor))
    if only:
        collected = [i for i in collected if i.type == only]
    return sorted(collected, key=lambda i: i.waiting_since)
