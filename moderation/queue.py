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
}


@dataclass
class Item:
    type: str
    obj: Any
    waiting_since: datetime


def _held(actor):
    posts = Post.objects.filter(is_held=True, thread__kind=Thread.Kind.DISCUSSION).select_related(
        "author", "thread__subforum"
    )
    return [Item("held", p, p.created_at) for p in posts if can(actor, "post.moderate", p)]


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
    return [Item("action", a, a.created_at) for a in pending if can(actor, "moderation.approve", a)]


def _promotions(actor):
    open_ = Promotion.objects.open().select_related("member", "recommended_by", "reviewed_by", "from_role", "to_role")
    found = []
    for promotion in open_:
        action = "promotion.review" if promotion.status == Promotion.Status.RECOMMENDED else "promotion.decide"
        if can(actor, action, promotion):
            found.append(Item("promotion", promotion, promotion.recommended_at or promotion.created_at))
    return found


def _sponsor_reviews(actor):
    pending = SponsorReview.objects.filter(status=SponsorReview.Status.PENDING).select_related("sponsor", "banned_member")
    return [Item("sponsor_review", r, r.created_at) for r in pending if can(actor, "sponsor_review.decide", r)]


def items(actor, only=None):
    if not can(actor, "queue.view"):
        return []
    sources = {"held": _held, "report": _reports, "flag": _reports, "action": _actions,
               "promotion": _promotions, "sponsor_review": _sponsor_reviews}
    collected, seen = [], set()
    for type_, source in sources.items():
        if only and type_ != only or source in seen:
            continue
        seen.add(source)
        collected.extend(source(actor))
    if only:
        collected = [i for i in collected if i.type == only]
    return sorted(collected, key=lambda i: i.waiting_since)
