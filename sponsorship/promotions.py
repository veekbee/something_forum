"""Promotion workflow (docs/DESIGN.md, Sponsorship, pedigree and onboarding).

Provisional to Full: eligibility is shown, a Full+ member recommends, a Moderator reviews with notes,
an Admin or Owner approves or declines. Full to Tenured: an Admin or Moderator promotes an eligible
member directly, and the member's active sponsorship ends. Nobody is promoted automatically.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts import roles
from accounts.models import Role, RoleAssignment, User
from audit import log
from boards.models import Post, Thread
from core.services import require
from moderation.models import ModerationAction
from sponsorship import capacity
from sponsorship.eligibility import current_assignment, full_promotion_eligible
from sponsorship.models import Promotion, Sponsorship


def eligible_for_full():
    """Provisional members who meet the qualifying stats and have no promotion in progress, for
    the eligibility list the system shows."""
    candidates = User.objects.filter(
        role_assignments__role__name=roles.PROVISIONAL,
        role_assignments__revoked_at__isnull=True,
        role_assignments__scope_subforum__isnull=True,
    ).exclude(promotions__status__in=[Promotion.Status.RECOMMENDED, Promotion.Status.REVIEWED]).distinct()
    return [member for member in candidates if full_promotion_eligible(member)]


def probation_record(member):
    """What the reviewing Moderator should see: moderation and held or rejected posts since the
    member became Provisional."""
    assignment = current_assignment(member, roles.PROVISIONAL)
    since = assignment.granted_at if assignment else member.joined_at
    actions = ModerationAction.objects.filter(target_user=member, created_at__gte=since).exclude(
        status=ModerationAction.Status.PENDING
    )
    posts = Post.objects.filter(author=member, thread__kind=Thread.Kind.DISCUSSION, created_at__gte=since)
    return {
        "since": since,
        "actions": list(actions.order_by("created_at")),
        "posts": posts.counted().count(),
        # Every post that was ever held: still held, released, or rejected (only held posts are).
        "held_posts": posts.filter(
            Q(is_held=True) | Q(released_at__isnull=False) | Q(rejected_at__isnull=False)
        ).count(),
        "rejected_posts": posts.filter(rejected_at__isnull=False).count(),
    }


def _locked(promotion):
    return Promotion.objects.select_for_update().select_related("member", "from_role", "to_role").get(
        pk=promotion.pk
    )


def _change_trust_role(actor, member, from_name, to_name, promotion):
    """Close the member's global from-role assignment and open a to-role one (design rule 5)."""
    now = timezone.now()
    for assignment in RoleAssignment.objects.filter(
        user=member, role__name=from_name, revoked_at__isnull=True, scope_subforum__isnull=True
    ):
        assignment.revoked_at, assignment.revoked_by = now, actor
        assignment.save()
    return RoleAssignment.objects.create(
        user=member,
        role=Role.objects.get(name=to_name),
        granted_by=actor,
        granted_at=now,
        reason=f"Promotion {promotion.pk}",
    )


@transaction.atomic
def recommend(actor, member):
    require(actor, "promotion.recommend", member)
    promotion = Promotion.objects.create(
        member=member,
        from_role=Role.objects.get(name=roles.PROVISIONAL),
        to_role=Role.objects.get(name=roles.FULL),
        recommended_by=actor,
        recommended_at=timezone.now(),
    )
    log.record(actor, "promotion.recommend", promotion, {"member": member.pk})
    return promotion


@transaction.atomic
def review(actor, promotion, notes=""):
    promotion = _locked(promotion)
    require(actor, "promotion.review", promotion)
    promotion.status = Promotion.Status.REVIEWED
    promotion.reviewed_by, promotion.reviewed_at, promotion.review_notes = actor, timezone.now(), notes
    promotion.save()
    log.record(actor, "promotion.review", promotion, {"member": promotion.member_id, "notes": notes})
    return promotion


@transaction.atomic
def decide(actor, promotion, approve, notes=""):
    promotion = _locked(promotion)
    require(actor, "promotion.decide", promotion)
    if approve:
        _change_trust_role(actor, promotion.member, promotion.from_role.name, promotion.to_role.name, promotion)
    promotion.status = Promotion.Status.APPROVED if approve else Promotion.Status.DECLINED
    promotion.decided_by, promotion.decided_at = actor, timezone.now()
    promotion.save()
    log.record(
        actor, "promotion.decide", promotion,
        {"member": promotion.member_id, "approved": approve, "notes": notes},
    )
    return promotion


@transaction.atomic
def promote_to_tenured(actor, member, notes=""):
    """Full to Tenured. Sponsorship is active only until Tenured, so the active one ends here and
    the sponsor's slot frees up; the original link stays in the pedigree."""
    require(actor, "promotion.tenured", member)
    now = timezone.now()
    promotion = Promotion.objects.create(
        member=member,
        from_role=Role.objects.get(name=roles.FULL),
        to_role=Role.objects.get(name=roles.TENURED),
        status=Promotion.Status.APPROVED,
        decided_by=actor,
        decided_at=now,
        review_notes=notes,
    )
    _change_trust_role(actor, member, roles.FULL, roles.TENURED, promotion)
    ended = None
    sponsorship = Sponsorship.objects.filter(member=member, ended_at__isnull=True).first()
    if sponsorship is not None:
        sponsorship.ended_at, sponsorship.end_reason = now, Sponsorship.EndReason.TENURED
        sponsorship.save()
        ended = sponsorship.pk
        capacity.reassign_slots(sponsorship.sponsor)
    log.record(actor, "promotion.tenured", promotion, {"member": member.pk, "sponsorship_ended": ended})
    return promotion
