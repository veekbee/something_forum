"""Forum pages: index, sub-forums, threads, the editor, thread actions, profiles, search and
attachments. Built plain and mobile-first; the look comes in a later design pass. Every decision is
made by core.permissions or boards.services; views collect input and show results."""

from django import forms
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts import roles
from accounts.models import User
from boards import services, visibility
from boards.models import Attachment, Post, PostRevision, SubForum, Thread
from boards.rendering import quote_source
from core import registry
from core.permissions import MoveRequest, can
from moderation.models import ModerationAction
from sponsorship import eligibility
from sponsorship.models import Promotion


def _require(user, action, target=None):
    decision = can(user, action, target)
    if not decision:
        raise PermissionDenied(decision.reason)
    return decision


def _page(request, items, key):
    """Pagination with the registry's page size; there is no "show all" (design: access control)."""
    paginator = Paginator(items, registry.site_value(key))
    return paginator.get_page(request.GET.get("page"))


def _live(posts):
    """Posts that count in public figures: released, not rejected, not deleted."""
    return posts.filter(is_held=False, rejected_at__isnull=True, deleted_at__isnull=True)


def _errors(exc):
    return exc.messages if isinstance(exc, ValidationError) else [str(exc)]


# --- index and sub-forums --------------------------------------------------------------------


@require_GET
def forum_index(request):
    user = request.user
    visible = _live(visibility.visible_posts(user))

    def summary(subforum):
        posts = visible.filter(thread__subforum=subforum)
        return {
            "subforum": subforum,
            "threads": visibility.visible_threads(user, subforum).count(),
            "posts": posts.count(),
            "latest": posts.select_related("thread", "author").order_by("-created_at").first(),
            "depth": 0 if subforum.parent_id is None else 1,
        }

    readable = visibility.readable_subforums(user)
    regular = [summary(sf) for sf in readable if not sf.is_ending_area]
    endings = [summary(sf) for sf in readable if sf.is_ending_area]
    return render(request, "boards/index.html", {"regular": regular, "endings": endings})


class PostForm(forms.Form):
    # Image files come from the template's own multiple-file input (request.FILES "images").
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 8, "data-mentions": "1"}), label="Post")


class ThreadForm(PostForm):
    title = forms.CharField(max_length=200)
    field_order = ["title", "body"]


def _files(request):
    return request.FILES.getlist("images")


@require_GET
def subforum_page(request, slug):
    subforum = get_object_or_404(SubForum, slug=slug)
    _require(request.user, "subforum.read", subforum)
    threads = visibility.visible_threads(request.user, subforum).select_related("author", "origin_subforum")
    return render(request, "boards/subforum.html", {
        "subforum": subforum,
        "page": _page(request, threads, "pagination.threads_per_subforum_page"),
        "may_start": can(request.user, "subforum.start_thread", subforum),
    })


def new_thread(request, slug):
    subforum = get_object_or_404(SubForum, slug=slug)
    _require(request.user, "subforum.start_thread", subforum)
    form = ThreadForm(request.POST or None, request.FILES or None)
    errors = []
    if request.method == "POST" and form.is_valid():
        try:
            thread, _ = services.start_thread(
                request.user, subforum, form.cleaned_data["title"], form.cleaned_data["body"], _files(request)
            )
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            return redirect("thread", pk=thread.pk)
    return render(request, "boards/post_form.html", {
        "form": form, "errors": errors, "subforum": subforum, "heading": f"New thread in {subforum.name}",
        "images": subforum.setting("subforum.images"),
    })


# --- threads ---------------------------------------------------------------------------------


def _thread_or_404(user, pk):
    # DM threads get their own pages with build step 4.
    thread = get_object_or_404(
        Thread.objects.select_related("subforum", "origin_subforum", "author"), pk=pk, kind=Thread.Kind.DISCUSSION
    )
    if not can(user, "thread.read", thread):
        raise Http404
    return thread


def _post_notes(posts):
    """Per post: shown as edited by staff, or redacted by staff (design: editing and deleting)."""
    revisions = PostRevision.objects.filter(post__in=posts).order_by("edited_at", "pk")
    latest, redacted = {}, set()
    for revision in revisions:
        latest[revision.post_id] = revision
        if revision.is_redaction:
            redacted.add(revision.post_id)
    notes = {}
    for post in posts:
        if post.pk in redacted:
            notes[post.pk] = "redacted by staff"
        elif post.pk in latest and latest[post.pk].edited_by_id != post.author_id:
            notes[post.pk] = "edited by staff"
    return notes


def thread_page(request, pk):
    thread = _thread_or_404(request.user, pk)
    services.open_thread(request.user, thread)
    user = request.user
    page = _page(request, visibility.thread_posts(user, thread).select_related("author", "deleted_by"),
                 "pagination.posts_per_thread_page")
    posts = list(page.object_list)
    notes = _post_notes(posts)
    may_reply = can(user, "thread.reply", thread)
    entries = [{
        "post": post,
        "placeholder": post.deleted_at is not None and not can(user, "post.read", post),
        "note": notes.get(post.pk),
        "may_edit": can(user, "post.edit", post),
        "may_delete": can(user, "post.delete", post),
        "may_quote": bool(may_reply) and not post.is_held and post.deleted_at is None,
        "may_moderate": post.is_held and can(user, "post.moderate", post),
        "may_revisions": can(user, "post.read_revisions", post),
        "may_report": can(user, "report.create", post),
    } for post in posts]

    initial = ""
    quote_id = request.GET.get("quote")
    if may_reply and quote_id and quote_id.isdigit():
        quoted = visibility.thread_posts(user, thread).filter(pk=int(quote_id), is_held=False).first()
        if quoted is not None:
            initial = quote_source(quoted)

    subforum = thread.subforum
    destinations = [sf for sf in SubForum.objects.filter(kind=SubForum.Kind.REGULAR)
                    if can(user, "thread.move", MoveRequest(thread, sf))]
    return render(request, "boards/thread.html", {
        "thread": thread,
        "page": page,
        "entries": entries,
        "form": PostForm(initial={"body": initial}) if may_reply else None,
        "images": subforum.setting("subforum.images") if subforum else "off",
        "may": {
            "reply": may_reply,
            "title": can(user, "thread.edit_title", thread),
            "titles": can(user, "thread.read_title_revisions", thread),
            "lock": can(user, "thread.lock", thread),
            "pin": can(user, "thread.pin", thread),
            "archive": can(user, "thread.archive", thread),
            "graveyard": can(user, "thread.graveyard", thread),
            "classics": can(user, "thread.classics", thread),
            "restore": can(user, "thread.restore", thread),
            "move": bool(destinations),
            "follow": can(user, "thread.follow", thread),
        },
        "following": thread.participants.filter(user=user).exists(),
    })


@require_POST
def reply(request, pk):
    thread = _thread_or_404(request.user, pk)
    form = PostForm(request.POST, request.FILES)
    try:
        if not form.is_valid():
            raise ValidationError("Write something before posting.")
        post = services.reply(request.user, thread, form.cleaned_data["body"], _files(request))
    except (ValidationError, PermissionDenied) as exc:
        return render(request, "boards/post_form.html", {
            "form": form, "errors": _errors(exc), "heading": f"Reply to {thread.title}", "thread": thread,
            "images": thread.subforum.setting("subforum.images") if thread.subforum else "off",
        }, status=400)
    return redirect("post_link", pk=post.pk)


@require_GET
def post_link(request, pk):
    """The address of a post: its page in the thread, with an anchor."""
    post = get_object_or_404(Post.objects.select_related("thread"), pk=pk)
    if not can(request.user, "post.read", post):
        raise Http404
    if post.thread.kind == Thread.Kind.DM:
        from boards import messages

        earlier = messages.messages(request.user, post.thread).filter(
            Q(created_at__lt=post.created_at) | Q(created_at=post.created_at, pk__lt=post.pk)
        ).count()
        page = earlier // registry.site_value("pagination.posts_per_thread_page") + 1
        return HttpResponseRedirect(f"{reverse('conversation', args=[post.thread_id])}?page={page}#post-{post.pk}")
    earlier = visibility.thread_posts(request.user, post.thread).filter(
        Q(created_at__lt=post.created_at) | Q(created_at=post.created_at, pk__lt=post.pk)
    ).count()
    page = earlier // registry.site_value("pagination.posts_per_thread_page") + 1
    return HttpResponseRedirect(f"{reverse('thread', args=[post.thread_id])}?page={page}#post-{post.pk}")


def edit_post(request, pk):
    post = get_object_or_404(Post.objects.select_related("thread__subforum"), pk=pk)
    _require(request.user, "post.edit", post)
    form = PostForm(request.POST or None, request.FILES or None, initial={"body": post.body_source})
    errors = []
    if request.method == "POST" and form.is_valid():
        try:
            services.edit_post(request.user, post, form.cleaned_data["body"], _files(request))
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            return redirect("post_link", pk=post.pk)
    subforum = post.thread.subforum
    return render(request, "boards/post_form.html", {
        "form": form, "errors": errors, "heading": "Edit post", "thread": post.thread,
        "images": subforum.setting("subforum.images") if subforum else "off",
        "existing_images": post.attachments.count(),
    })


def delete_post(request, pk):
    post = get_object_or_404(Post.objects.select_related("thread__subforum", "author"), pk=pk)
    decision = _require(request.user, "post.delete", post)
    needs_reason = decision.via == "staff" and post.author_id != request.user.pk
    errors = []
    if request.method == "POST":
        try:
            services.delete_post(request.user, post, request.POST.get("reason_key", ""), request.POST.get("note", ""))
        except ValidationError as exc:
            errors = _errors(exc)
        else:
            if post.thread.kind == Thread.Kind.DM:
                return redirect("conversation", pk=post.thread_id)
            return redirect("thread", pk=post.thread_id)
    from moderation.models import REASONS

    return render(request, "boards/confirm.html", {
        "heading": "Hide this post?" if needs_reason else "Delete this post?", "errors": errors,
        "reasons": REASONS if needs_reason else None,
        "explanation": "The post disappears from the thread and its author is told why. Admins can still see it."
        if needs_reason else "The post disappears from the thread. Admins can still see it.",
        "button": "Hide post" if needs_reason else "Delete post", "back": reverse("post_link", args=[post.pk]),
    })


@require_POST
def moderate_post(request, pk, decision):
    if decision not in ("release", "reject"):
        raise Http404
    post = get_object_or_404(Post.objects.select_related("thread__subforum"), pk=pk)
    if decision == "release":
        services.release_post(request.user, post)
        return redirect("post_link", pk=post.pk)
    services.reject_post(request.user, post, request.POST.get("reason", ""))
    return redirect("thread", pk=post.thread_id)


def post_revisions(request, pk):
    post = get_object_or_404(Post.objects.select_related("thread__subforum", "author"), pk=pk)
    _require(request.user, "post.read_revisions", post)
    if request.method == "POST":
        services.purge_revisions(request.user, post)
        return redirect("post_revisions", pk=post.pk)
    return render(request, "boards/revisions.html", {
        "post": post,
        "revisions": post.revisions.select_related("edited_by", "purged_by").order_by("edited_at", "pk"),
        "may_purge": can(request.user, "post.purge_revisions", post),
    })


def title_revisions(request, pk):
    thread = _thread_or_404(request.user, pk)
    _require(request.user, "thread.read_title_revisions", thread)
    return render(request, "boards/title_revisions.html", {
        "thread": thread, "revisions": thread.title_revisions.select_related("edited_by").order_by("edited_at", "pk"),
    })


# --- thread actions --------------------------------------------------------------------------


class TitleForm(forms.Form):
    title = forms.CharField(max_length=200)


def edit_title(request, pk):
    thread = _thread_or_404(request.user, pk)
    _require(request.user, "thread.edit_title", thread)
    form = TitleForm(request.POST or None, initial={"title": thread.title})
    errors = []
    if request.method == "POST" and form.is_valid():
        try:
            services.edit_title(request.user, thread, form.cleaned_data["title"])
        except ValidationError as exc:
            errors = _errors(exc)
        else:
            return redirect("thread", pk=thread.pk)
    return render(request, "boards/simple_form.html", {
        "form": form, "errors": errors, "heading": "Edit thread title", "button": "Save title",
        "back": reverse("thread", args=[thread.pk]),
    })


@require_POST
def toggle(request, pk, what):
    thread = _thread_or_404(request.user, pk)
    if what == "lock":
        services.set_locked(request.user, thread, thread.state != Thread.State.LOCKED)
    elif what == "pin":
        services.set_pinned(request.user, thread, not thread.is_pinned)
    elif what == "restore":
        services.restore_thread(request.user, thread)
    elif what == "follow":
        services.set_following(request.user, thread, not thread.participants.filter(user=request.user).exists())
    else:
        raise Http404
    return redirect("thread", pk=thread.pk)


ENDINGS = {
    "archive": ("thread.archive", None, "Archive this thread in place?",
                "It becomes read-only where it is. Only an Owner can undo this."),
    "graveyard": ("thread.graveyard", SubForum.Kind.GRAVEYARD, "Send this thread to the Graveyard?",
                  "This is what deleting a thread means. It is kept for good, read-only, in the Thread Graveyard."),
    "classics": ("thread.classics", SubForum.Kind.CLASSICS, "Send this thread to the Classics?",
                 "An honour for a thread of especially high quality that has reached its end. It becomes read-only."),
}


def end_thread(request, pk, how):
    if how not in ENDINGS:
        raise Http404
    action, area_kind, heading, explanation = ENDINGS[how]
    thread = _thread_or_404(request.user, pk)
    _require(request.user, action, thread)
    widens = area_kind is not None and services.widens_audience(thread.subforum, SubForum.objects.get(kind=area_kind))
    errors = []
    if request.method == "POST":
        reason = request.POST.get("reason", "")
        try:
            {"archive": services.archive_thread, "graveyard": services.send_to_graveyard,
             "classics": services.send_to_classics}[how](request.user, thread, reason)
        except ValidationError as exc:
            errors = _errors(exc)
        else:
            return redirect("thread", pk=thread.pk)
    return render(request, "boards/confirm.html", {
        "heading": heading, "explanation": explanation, "errors": errors,
        "warning": "Provisional members and above can read it there, which is more people than can read it now."
        if widens else "",
        "reason_field": True, "reason_required": how == "graveyard", "button": "Confirm",
        "back": reverse("thread", args=[thread.pk]),
    })


def move_thread(request, pk):
    thread = _thread_or_404(request.user, pk)
    options = [sf for sf in SubForum.objects.filter(kind=SubForum.Kind.REGULAR)
               if can(request.user, "thread.move", MoveRequest(thread, sf))]
    if not options:
        raise PermissionDenied("You cannot move this thread.")
    chosen = next((sf for sf in options if str(sf.pk) == (request.POST.get("to") or request.GET.get("to"))), None)
    if request.method == "POST" and chosen is not None:
        services.move_thread(request.user, thread, chosen)
        return redirect("thread", pk=thread.pk)
    return render(request, "boards/move.html", {
        "thread": thread, "options": options, "chosen": chosen,
        "widens": chosen is not None and services.widens_audience(thread.subforum, chosen),
    })


# --- members ---------------------------------------------------------------------------------


@require_GET
def member_profile(request, slug):
    member = get_object_or_404(User, slug=slug)
    if not can(request.user, "member.view_profile", member):
        raise Http404
    role = roles.trust_role(member)
    context = {
        "member": member,
        "role": role,
        "classics": Thread.objects.filter(subforum__kind=SubForum.Kind.CLASSICS, author=member).order_by("-ended_at"),
        "page": _page(request, visibility.post_history(request.user, member).select_related("thread"),
                      "pagination.profile_posts_per_page"),
        "own": member.pk == request.user.pk,
    }
    if can(request.user, "member.view_record", member):
        context["record_count"] = ModerationAction.objects.filter(
            target_user=member, is_public=True, status__in=ModerationAction.RECORD_STATUSES
        ).count()
    if can(request.user, "member.private_stats", member):
        context["private"] = _private_stats(member, role)
    if member.pk != request.user.pk:
        from accounts.models import Block
        from boards import dm

        context["may_message"] = dm.may_message(request.user, member)
        context["may_block"] = bool(can(request.user, "member.block", member))
        context["blocked"] = Block.objects.filter(blocker=request.user, blocked=member).exists()
        context["may_report"] = bool(can(request.user, "report.create", member))
        from core.permissions import ActionRequest

        context["may_act"] = any(
            can(request.user, "moderation.initiate", ActionRequest(kind, member)) for kind in ("note", "warning")
        )
    else:
        context["may_customise"] = bool(can(request.user, "profile.customise"))
    return render(request, "boards/profile.html", context)


def _private_stats(member, role):
    """The member's own post count and progress toward promotion; nobody else sees these."""
    from django.utils import timezone

    stats = {"post_count": Post.objects.counted().filter(author=member, thread__kind=Thread.Kind.DISCUSSION).count()}
    if role is not None and role.name in (roles.PROVISIONAL, roles.FULL):
        assignment = eligibility.current_assignment(member, role.name)
        days = (timezone.now() - assignment.granted_at).days if assignment else 0
        if role.name == roles.PROVISIONAL:
            since = assignment.granted_at if assignment else member.joined_at
            stats["progress"] = {
                "toward": "Full",
                "days": days, "days_needed": registry.site_value("promotion.full.min_days"),
                "posts": Post.objects.counted().filter(
                    author=member, thread__kind=Thread.Kind.DISCUSSION, created_at__gte=since).count(),
                "posts_needed": registry.site_value("promotion.full.min_posts"),
                "in_progress": Promotion.objects.open().filter(member=member).exists(),
            }
        else:
            stats["progress"] = {"toward": "Tenured", "days": days,
                                 "days_needed": registry.site_value("promotion.tenured.min_days")}
    return stats


@require_GET
def mention_autocomplete(request):
    """Members whose slug starts with the typed text. Invited accounts are never offered."""
    _require(request.user, "search.use")
    prefix = request.GET.get("q", "").strip().lower()
    if not prefix:
        return JsonResponse({"results": []})
    users = User.objects.filter(slug__startswith=prefix).exclude(
        status__in=[User.Status.INVITED, User.Status.REMOVED, User.Status.TOMBSTONE]
    ).order_by("slug")[:8]
    return JsonResponse({"results": [{"slug": u.slug, "name": u.display_name} for u in users]})


# --- search and attachments ------------------------------------------------------------------


@require_GET
def search(request):
    _require(request.user, "search.use")
    query = request.GET.get("q", "").strip()
    page = None
    if query:
        page = _page(request, visibility.search_posts(request.user, query), "pagination.search_results_per_page")
        shown = sorted({p.thread_id for p in page.object_list if p.thread.kind == Thread.Kind.DM})
        if shown:
            # Rule 8: a search whose results show DM text counts as a read, one entry per page shown.
            from django.db import transaction

            from audit import log

            with transaction.atomic():
                log.record(request.user, "dm.search_read", request.user, {"threads": shown}, ip=request.META.get("REMOTE_ADDR"))
    return render(request, "boards/search.html", {"query": query, "page": page})


@require_GET
def attachment(request, pk):
    """Images are served only after a permission check on their post (design rule 22)."""
    item = get_object_or_404(Attachment.objects.select_related("post__thread"), pk=pk)
    if item.post is None:
        # An avatar: shown to any signed-in member, and only while it is someone's current avatar.
        if not User.objects.filter(avatar=item).exists():
            raise Http404
    elif not can(request.user, "post.read", item.post):
        raise Http404
    if settings.AWS_STORAGE_BUCKET_NAME:
        # Object storage: a short-lived signed URL (settings: querystring_expire).
        return HttpResponseRedirect(default_storage.url(item.storage_key))
    response = FileResponse(default_storage.open(item.storage_key, "rb"), content_type=item.mime)
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = "inline"
    return response


@require_GET
def rap_sheet(request, slug):
    """The public disciplinary record's own page (rule 48), newest first. A link to the offending
    post passes the reader's normal permission check; removed posts and DM offences are never
    linked."""
    member = get_object_or_404(User, slug=slug)
    decision = can(request.user, "member.view_record", member)
    if not decision:
        raise Http404
    entries = []
    actions = ModerationAction.objects.filter(
        target_user=member, is_public=True, status__in=ModerationAction.RECORD_STATUSES
    ).select_related("initiated_by", "approved_by", "related_post__thread").prefetch_related(
        "scope_subforums").order_by("-starts_at", "-pk")
    for action in actions:
        post, where, link = action.related_post, "", None
        if post is not None:
            if post.thread.kind == Thread.Kind.DM:
                where = "in a private message"
            elif post.deleted_at is not None:
                where = "in a post since removed by staff"
            elif can(request.user, "post.read", post):
                where, link = "in a post", reverse("post_link", args=[post.pk])
        entries.append({"action": action, "where": where, "link": link})
    return render(request, "boards/rap_sheet.html", {"member": member, "entries": entries})
