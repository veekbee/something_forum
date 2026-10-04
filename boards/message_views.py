"""Direct-message pages. Decisions are made by core.permissions and boards.messages."""

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User
from boards import messages, services
from boards.models import Thread
from boards.views import PostForm, _errors, _files, _page
from core.permissions import Membership, can


def _conversation_or_404(user, pk):
    thread = get_object_or_404(Thread, pk=pk, kind=Thread.Kind.DM)
    if not can(user, "thread.read", thread):
        raise Http404
    return thread


def _members(text):
    """Turn "alex, sam" into members; unknown or hidden names are reported, not guessed."""
    slugs = [s.strip().lstrip("@").lower() for s in text.replace(",", " ").split() if s.strip()]
    found = {u.slug: u for u in User.objects.filter(slug__in=slugs).exclude(
        status__in=[User.Status.INVITED, User.Status.REMOVED, User.Status.TOMBSTONE])}
    missing = [s for s in slugs if s not in found]
    if missing:
        raise ValidationError(f"No member called {', '.join(missing)}.")
    return list(found.values())


def inbox(request):
    return render(request, "boards/messages/inbox.html", {
        "page": _page(request, messages.conversations(request.user), "pagination.threads_per_subforum_page"),
    })


class NewConversationForm(PostForm):
    to = forms.CharField(label="To", help_text="Member names from their profile addresses, separated by commas.")
    subject = forms.CharField(max_length=200, required=False)
    field_order = ["to", "subject", "body"]


def new_conversation(request):
    form = NewConversationForm(request.POST or None, request.FILES or None,
                               initial={"to": request.GET.get("to", "")})
    errors = []
    if request.method == "POST" and form.is_valid():
        try:
            others = _members(form.cleaned_data["to"])
            thread, _ = messages.start(
                request.user, others, form.cleaned_data["subject"], form.cleaned_data["body"], _files(request)
            )
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            return redirect("conversation", pk=thread.pk)
    return render(request, "boards/messages/new.html", {
        "form": form, "errors": errors, "notice": messages.SHORT_NOTICE, "images": "inline",
    })


def conversation(request, pk):
    thread = _conversation_or_404(request.user, pk)
    services.open_thread(request.user, thread)
    page = _page(request, messages.messages(request.user, thread).select_related("author"),
                 "pagination.posts_per_thread_page")
    messages.mark_read(request.user, thread)
    user = request.user
    participants = list(thread.participants.select_related("user", "added_by").order_by("joined_at"))
    active = [p for p in participants if p.left_at is None]
    may_send = can(user, "thread.reply", thread)
    return render(request, "boards/messages/conversation.html", {
        "thread": thread,
        "page": page,
        "entries": [{"post": p, "may_edit": can(user, "post.edit", p), "may_delete": can(user, "post.delete", p)}
                    for p in page.object_list],
        "participants": participants,
        "active": active,
        "removable": [p.user for p in active if can(user, "dm.remove", Membership(thread, p.user))],
        "may_send": may_send,
        "cannot_send": "" if may_send else may_send.reason,
        "may_leave": can(user, "dm.leave", thread),
        "may_add": thread.participants.filter(user=user, left_at__isnull=True).exists(),
        "form": PostForm() if may_send else None,
        "notice": messages.NOTICE,
        "short_notice": messages.SHORT_NOTICE,
        "images": "inline",
    })


@require_POST
def send(request, pk):
    thread = _conversation_or_404(request.user, pk)
    form = PostForm(request.POST, request.FILES)
    try:
        if not form.is_valid():
            raise ValidationError("Write something before sending.")
        post = services.reply(request.user, thread, form.cleaned_data["body"], _files(request))
    except (ValidationError, PermissionDenied) as exc:
        return render(request, "boards/post_form.html", {
            "form": form, "errors": _errors(exc), "heading": f"Message in {thread.title}", "images": "inline",
        }, status=400)
    return redirect("post_link", pk=post.pk)


@require_POST
def add(request, pk):
    thread = _conversation_or_404(request.user, pk)
    try:
        for user in _members(request.POST.get("who", "")):
            messages.add(request.user, thread, user)
    except ValidationError as exc:
        raise PermissionDenied(" ".join(_errors(exc))) from exc
    return redirect("conversation", pk=thread.pk)


@require_POST
def leave(request, pk):
    thread = _conversation_or_404(request.user, pk)
    messages.leave(request.user, thread)
    return redirect("inbox")


@require_POST
def remove(request, pk, user_pk):
    thread = _conversation_or_404(request.user, pk)
    messages.remove(request.user, thread, get_object_or_404(User, pk=user_pk))
    return redirect("conversation", pk=thread.pk)


@require_POST
def block(request, slug):
    target = get_object_or_404(User, slug=slug)
    if request.POST.get("undo"):
        messages.unblock(request.user, target)
    else:
        messages.block(request.user, target)
    return redirect("member_profile", slug=slug)
