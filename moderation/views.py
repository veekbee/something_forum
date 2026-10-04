"""The moderation queue and member reports. Decisions are made by core.permissions and the
moderation, boards and sponsorship services."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from accounts.models import User
from boards.models import Post
from core.permissions import can
from moderation import queue, reports, services
from moderation.models import REASONS, ModerationAction, Report, SponsorReview
from sponsorship import promotions
from sponsorship.models import Promotion


def _back(request):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect("queue")


def _errors(exc):
    return exc.messages if isinstance(exc, ValidationError) else [str(exc)]


def queue_page(request):
    decision = can(request.user, "queue.view")
    if not decision:
        raise PermissionDenied(decision.reason)
    only = request.GET.get("type") if request.GET.get("type") in queue.TYPES else None
    items = queue.items(request.user, only)
    links = {"held": "post", "action": "related_action", "promotion": "related_promotion"}
    for item in items:
        if item.type in ("report", "flag"):
            item.may_resolve = can(request.user, "report.resolve", item.obj)
            item.may_escalate = can(request.user, "report.escalate", item.obj)
        elif item.type in links:
            item.escalated = Report.objects.waiting().filter(
                kind=Report.Kind.ESCALATION, **{links[item.type]: item.obj}).exists()
            item.may_escalate = can(request.user, "queue.escalate", item.obj)
    mine = ModerationAction.objects.filter(initiated_by=request.user, status=ModerationAction.Status.PENDING)
    return render(request, "moderation/queue.html", {
        "items": items, "types": queue.TYPES, "only": only, "reasons": REASONS, "mine": mine.select_related("target_user"),
        "error": request.GET.get("error", ""),
    })


def _act(request, fn):
    """Run a queue action; a second Moderator acting on a handled item sees it already handled."""
    try:
        fn()
    except (ValidationError, PermissionDenied) as exc:
        message = " ".join(_errors(exc))
        return redirect(f"{reverse('queue')}?{urlencode({'error': message})}")
    return _back(request)


@require_POST
def report_action(request, pk, action):
    item = get_object_or_404(Report, pk=pk)
    post = request.POST
    handlers = {
        "resolve": lambda: reports.resolve(request.user, item, post.get("outcome", ""),
                                           post.get("hide_reason", ""), post.get("hide_note", "")),
        "escalate": lambda: reports.escalate(request.user, item, post.get("note", "")),
        "note": lambda: reports.add_note(request.user, item, post.get("note", "")),
    }
    if action not in handlers:
        raise PermissionDenied("unknown action")
    return _act(request, handlers[action])


@require_POST
def escalate_item(request, type_, pk):
    models = {"held": Post, "action": ModerationAction, "promotion": Promotion}
    if type_ not in models:
        raise PermissionDenied("unknown item")
    item = get_object_or_404(models[type_], pk=pk)
    return _act(request, lambda: reports.escalate_item(request.user, item, request.POST.get("note", "")))


@require_POST
def approve_action(request, pk):
    action = get_object_or_404(ModerationAction, pk=pk)
    summary = request.POST.get("public_summary")
    return _act(request, lambda: services.approve_action(request.user, action, summary))


@require_POST
def decline_action(request, pk):
    action = get_object_or_404(ModerationAction, pk=pk)
    return _act(request, lambda: services.decline_action(request.user, action, request.POST.get("reason", "")))


@require_POST
def withdraw_action(request, pk):
    action = get_object_or_404(ModerationAction, pk=pk)
    return _act(request, lambda: services.withdraw_action(request.user, action))


@require_POST
def lift_ban(request, pk):
    ban = get_object_or_404(ModerationAction, pk=pk)
    return _act(request, lambda: services.lift_ban(request.user, ban, request.POST.get("reason", "")))


def take_action(request, slug):
    """Propose (or, for Admins and Owners, take) a moderation action on a member."""
    from datetime import timedelta

    from django.utils import timezone

    from boards.models import SubForum
    from core.permissions import ActionRequest

    member = get_object_or_404(User, slug=slug)
    kinds = [(k, label) for k, label in ModerationAction.Kind.choices
             if can(request.user, "moderation.initiate", ActionRequest(k, member))
             or (k in ("hold", "suspension") and any(
                 can(request.user, "moderation.initiate", ActionRequest(k, member, scope_subforums=(sf,)))
                 for sf in SubForum.objects.filter(kind=SubForum.Kind.REGULAR)))]
    if not kinds:
        raise PermissionDenied("You cannot take action on this member.")
    scopes = [sf for sf in SubForum.objects.filter(kind=SubForum.Kind.REGULAR)
              if can(request.user, "moderation.initiate", ActionRequest("suspension", member, scope_subforums=(sf,)))]
    errors = []
    if request.method == "POST":
        post = request.POST
        chosen = set(post.getlist("scope"))
        scope = [sf for sf in scopes if str(sf.pk) in chosen]
        days = post.get("days", "")
        try:
            services.initiate_action(
                request.user, member, post.get("kind", ""), internal_reason=post.get("internal_reason", ""),
                public_summary=post.get("public_summary", ""), scope_subforums=scope,
                ends_at=timezone.now() + timedelta(days=int(days)) if days.isdigit() else None,
            )
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            return redirect("queue")
    active_bans = ModerationAction.objects.in_force().filter(target_user=member, kind=ModerationAction.Kind.BAN)
    permanent = ModerationAction.objects.in_force().filter(target_user=member, kind=ModerationAction.Kind.PERMANENT_BAN)
    return render(request, "moderation/take_action.html", {
        "member": member, "kinds": kinds, "scopes": scopes, "errors": errors,
        "liftable": [b for b in active_bans if can(request.user, "moderation.lift_ban", b)],
        "annullable": [p for p in permanent if can(request.user, "moderation.annul", p)],
    })


@require_POST
def annul_permanent_ban(request, pk):
    from moderation import permanent

    action = get_object_or_404(ModerationAction, pk=pk)
    return _act(request, lambda: permanent.annul(request.user, action, request.POST.get("reason", "")))


@require_POST
def promotion_action(request, pk, step):
    promotion = get_object_or_404(Promotion, pk=pk)
    notes = request.POST.get("notes", "")
    if step == "review":
        return _act(request, lambda: promotions.review(request.user, promotion, notes))
    if step in ("approve", "decline"):
        return _act(request, lambda: promotions.decide(request.user, promotion, step == "approve", notes))
    raise PermissionDenied("unknown step")


@require_POST
def sponsor_review_action(request, pk):
    review = get_object_or_404(SponsorReview, pk=pk)
    post = request.POST
    months = int(post["months"]) if post.get("months", "").isdigit() else None
    return _act(request, lambda: services.decide_sponsor_review(
        request.user, review, post.get("outcome", ""), notes=post.get("notes", ""),
        public_summary=post.get("public_summary", ""), months=months,
        invitees_transfer=post.get("invitees_transfer") == "on" if months else False,
    ))


def _report_page(request, target, heading):
    decision = can(request.user, "report.create", target)
    if not decision:
        raise PermissionDenied(decision.reason)
    errors, done = [], None
    if request.method == "POST":
        try:
            _, created = reports.report(request.user, target, request.POST.get("reason", ""), request.POST.get("note", ""))
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            done = "Thank you. Staff will look at it." if created else "You have already reported this."
    return render(request, "moderation/report_form.html", {
        "heading": heading, "reasons": REASONS, "errors": errors, "done": done,
    })


def report_post(request, pk):
    post = get_object_or_404(Post.objects.select_related("thread"), pk=pk)
    return _report_page(request, post, "Report a message" if post.thread.kind == "dm" else "Report a post")


def report_member(request, slug):
    member = get_object_or_404(User, slug=slug)
    return _report_page(request, member, f"Report {member.display_name}")


# --- staff views (rule 37) -------------------------------------------------------------------


def member_view(request, slug):
    """Two tiers: Moderators see posts in sub-forums they moderate, moderation history, sponsor and
    invitees; Admins and Owners also see DMs, blocks, payment, sessions and, on request, identity
    details. Revealing identity details is audited; opening the view is not."""
    from django.db import transaction

    from accounts.models import Block, IdentityRecord, UserSession
    from audit import log
    from billing.models import Charge, Subscription
    from boards.models import Thread
    from boards.views import _page
    from boards.visibility import moderated_subforums
    from sponsorship.models import Invitation, Sponsorship

    member = get_object_or_404(User, slug=slug)
    view = can(request.user, "member.staff_view", member)
    if not view:
        raise PermissionDenied(view.reason)
    full = view.via == "full"
    posts = Post.objects.filter(author=member, thread__kind=Thread.Kind.DISCUSSION)
    if not full:
        posts = posts.filter(thread__subforum__in=moderated_subforums(request.user), deleted_at__isnull=True)
    context = {
        "member": member,
        "full": full,
        "page": _page(request, posts.select_related("thread__subforum").order_by("-created_at"),
                      "pagination.profile_posts_per_page"),
        "actions": ModerationAction.objects.filter(target_user=member).select_related(
            "initiated_by", "approved_by", "declined_by").prefetch_related("scope_subforums").order_by("-created_at"),
        "sponsorships": Sponsorship.objects.filter(member=member).select_related("sponsor").order_by("started_at"),
        "invitees": Sponsorship.objects.filter(sponsor=member).select_related("member").order_by("started_at"),
        "invitations": Invitation.objects.filter(sponsor=member).order_by("-created_at"),
        "may_reset": bool(can(request.user, "profile.reset_extra", member)) and bool(member.avatar_id or member.caption),
    }
    if full:
        context.update({
            "conversations": Thread.objects.filter(kind=Thread.Kind.DM, participants__user=member).distinct()
            .order_by("-last_post_at"),
            "blocks_made": Block.objects.filter(blocker=member).select_related("blocked"),
            "blocks_received": Block.objects.filter(blocked=member).select_related("blocker"),
            "subscription": Subscription.objects.filter(user=member).first(),
        "charges": Charge.objects.filter(user=member).order_by("-created_at"),
        "entitlements": member.entitlements.filter(revoked_at__isnull=True).select_related("extra"),
        "public_actions": ModerationAction.objects.filter(target_user=member, is_public=True,
                                                          status__in=ModerationAction.RECORD_STATUSES),
            "sessions": UserSession.objects.filter(user=member).order_by("-last_seen_at")[:20],
            "has_identity": IdentityRecord.objects.filter(user=member).exists(),
        })
        if request.method == "POST" and request.POST.get("reveal") and context["has_identity"]:
            if not can(request.user, "identity.read", member):
                raise PermissionDenied("identity data is for Admins only")
            identity = IdentityRecord.objects.get(user=member)
            with transaction.atomic():
                # The entry names the member by id only; never the real name.
                log.record(request.user, "identity.reveal", member, ip=request.META.get("REMOTE_ADDR"))
            context["identity"] = identity
    return render(request, "moderation/member.html", context)


def audit_log(request):
    """Admins and Owners; filtered by actor, member concerned, action and date range, newest first."""
    from django.db.models import Q
    from django.utils.dateparse import parse_date

    from audit.models import AuditEntry
    from boards.views import _page

    decision = can(request.user, "audit.view")
    if not decision:
        raise PermissionDenied(decision.reason)
    entries = AuditEntry.objects.select_related("actor").order_by("-created_at", "-pk")
    params = request.GET
    if params.get("actor"):
        entries = entries.filter(actor__slug=params["actor"])
    if params.get("member"):
        member = User.objects.filter(slug=params["member"]).first()
        pk = member.pk if member else -1
        entries = entries.filter(
            Q(target_type="accounts.user", target_id=str(pk)) | Q(payload__target_user=pk) | Q(payload__member=pk)
            | Q(payload__invitee=pk) | Q(payload__user=pk) | Q(payload__author=pk) | Q(payload__sponsor=pk)
        )
    if params.get("action"):
        entries = entries.filter(action__startswith=params["action"])
    if params.get("from") and parse_date(params["from"]):
        entries = entries.filter(created_at__date__gte=parse_date(params["from"]))
    if params.get("to") and parse_date(params["to"]):
        entries = entries.filter(created_at__date__lte=parse_date(params["to"]))
    return render(request, "moderation/audit_log.html", {
        "page": _page(request, entries, "pagination.search_results_per_page"), "params": params,
        "pagination_query": urlencode({k: v for k, v in params.items() if k != "page" and v}),
    })


def feed_page(request):
    from moderation import feed

    decision = can(request.user, "feed.view")
    if not decision:
        raise PermissionDenied(decision.reason)
    return render(request, "moderation/feed.html", {"items": feed.items(request.user)})



@require_POST
def reset_extra(request, slug):
    from billing import extras

    member = get_object_or_404(User, slug=slug)
    return _act(request, lambda: extras.reset_to_default(request.user, member))


@require_POST
def revoke_extra(request, pk):
    from billing import extras
    from billing.models import Entitlement

    entitlement = get_object_or_404(Entitlement, pk=pk)
    action = ModerationAction.objects.filter(pk=request.POST.get("action") or 0).first()
    if action is None:
        raise PermissionDenied("Choose the moderation action this revocation rests on.")
    return _act(request, lambda: extras.revoke(request.user, entitlement, action))
