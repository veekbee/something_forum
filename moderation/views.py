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
    for item in items:
        if item.type in ("report", "flag"):
            item.may_resolve = can(request.user, "report.resolve", item.obj)
            item.may_escalate = can(request.user, "report.escalate", item.obj)
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
                 can(request.user, "moderation.initiate", ActionRequest(k, member, scope_subforum=sf))
                 for sf in SubForum.objects.filter(kind=SubForum.Kind.REGULAR)))]
    if not kinds:
        raise PermissionDenied("You cannot take action on this member.")
    scopes = [sf for sf in SubForum.objects.filter(kind=SubForum.Kind.REGULAR)
              if can(request.user, "moderation.initiate", ActionRequest("suspension", member, scope_subforum=sf))]
    errors = []
    if request.method == "POST":
        post = request.POST
        scope = next((sf for sf in scopes if str(sf.pk) == post.get("scope")), None)
        days = post.get("days", "")
        try:
            services.initiate_action(
                request.user, member, post.get("kind", ""), internal_reason=post.get("internal_reason", ""),
                public_summary=post.get("public_summary", ""), scope_subforum=scope,
                ends_at=timezone.now() + timedelta(days=int(days)) if days.isdigit() else None,
            )
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            return redirect("queue")
    active_bans = ModerationAction.objects.in_force().filter(target_user=member, kind=ModerationAction.Kind.BAN)
    return render(request, "moderation/take_action.html", {
        "member": member, "kinds": kinds, "scopes": scopes, "errors": errors,
        "liftable": [b for b in active_bans if can(request.user, "moderation.lift_ban", b)],
    })


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
