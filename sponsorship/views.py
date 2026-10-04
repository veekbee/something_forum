"""Onboarding pages. Every decision is made by the permission service or the onboarding services;
these views only collect input and show results."""

from allauth.account.utils import perform_login
from django import forms
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.models import User
from core.permissions import can
from sponsorship import capacity, onboarding
from sponsorship.models import Invitation


def _errors(exc):
    return exc.messages if isinstance(exc, ValidationError) else [str(exc)]


# --- sponsor -------------------------------------------------------------------------------


class InvitationForm(forms.Form):
    invitee_email = forms.EmailField(label="Their email address")
    vouching_notes = forms.CharField(
        label="How do you know them, and for how long?", widget=forms.Textarea(attrs={"rows": 4})
    )


def invitations(request):
    """The sponsor's invitations, and the form to send one."""
    may_sponsor = can(request.user, "member.sponsor")
    form = InvitationForm(request.POST or None)
    errors = []
    if request.method == "POST":
        if not may_sponsor:
            raise PermissionDenied(may_sponsor.reason)
        if form.is_valid():
            try:
                _, holds_slot = onboarding.send_invitation(
                    request.user,
                    form.cleaned_data["invitee_email"],
                    form.cleaned_data["vouching_notes"],
                    accept_url=lambda token: request.build_absolute_uri(reverse("invitation_accept", args=[token])),
                )
            except ValidationError as exc:
                errors = _errors(exc)
            else:
                return redirect(f"{reverse('invitations')}?sent={'slot' if holds_slot else 'wait'}")
    sent = list(Invitation.objects.filter(sponsor=request.user).select_related("invitee").order_by("-created_at"))
    for invitation in sent:
        invitation.may_gift = invitation.invitee is not None and bool(can(request.user, "billing.gift", invitation.invitee))
    return render(request, "sponsorship/invitations.html", {
        "form": form,
        "errors": errors,
        "may_sponsor": may_sponsor,
        "at_capacity": capacity.at_capacity(request.user) if may_sponsor else False,
        "cap": capacity.cap_for(request.user),
        "sent": sent,
        "just_sent": request.GET.get("sent"),
        "live": Invitation.LIVE,
    })


@require_POST
def rescind(request, pk):
    invitation = get_object_or_404(Invitation, pk=pk, sponsor=request.user)
    onboarding.rescind(request.user, invitation)
    return redirect("invitations")


# --- invitee, before an account exists -----------------------------------------------------


class AcceptForm(forms.Form):
    display_name = forms.CharField(max_length=80, label="Display name (shown to members)")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Password again")

    def clean(self):
        data = super().clean()
        if data.get("password1") != data.get("password2"):
            raise ValidationError("The passwords do not match.")
        return data


def accept(request, token):
    """The one onboarding page served without a session (design rule 15)."""
    invitation = onboarding.invitation_for_token(token)
    if invitation is None:
        return render(request, "sponsorship/link_invalid.html", status=404)
    form = AcceptForm(request.POST or None)
    errors = []
    if request.method == "POST" and form.is_valid():
        try:
            accepted = onboarding.accept(token, form.cleaned_data["display_name"], form.cleaned_data["password1"])
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            if request.user.is_authenticated:
                logout(request)
            return perform_login(
                request, accepted.invitee, email_verification="none", redirect_url=reverse("onboarding_status")
            )
    return render(request, "sponsorship/accept.html", {
        "invitation": invitation,
        "form": form,
        "errors": errors,
        "token": token,
        "expires_at": onboarding.expires_at(invitation),
    })


@require_POST
def decline_link(request, token):
    invitation = onboarding.invitation_for_token(token)
    if invitation is None:
        return render(request, "sponsorship/link_invalid.html", status=404)
    onboarding.invitee_decline(invitation, token=token)
    return render(request, "sponsorship/declined.html")


# --- invitee, signed in and waiting --------------------------------------------------------


class IdentityForm(forms.Form):
    real_name = forms.CharField(
        max_length=200, label="Your real name",
        help_text="Only the forum's Admins see this. It is kept encrypted.",
    )


def onboarding_status(request):
    if request.user.status != User.Status.INVITED:
        return redirect("home")
    invitation = request.user.invitation
    form = IdentityForm(request.POST or None)
    errors = []
    if request.method == "POST" and form.is_valid():
        try:
            onboarding.submit_identity(request.user, form.cleaned_data["real_name"])
        except (ValidationError, PermissionDenied) as exc:
            errors = _errors(exc)
        else:
            return redirect("onboarding_status")
    return render(request, "sponsorship/onboarding_status.html", {
        "invitation": invitation,
        "submitted": request.user.identity.submitted_at is not None,
        "form": form,
        "errors": errors,
    })


@require_POST
def onboarding_decline(request):
    onboarding.invitee_decline(request.user.invitation, invitee=request.user)
    logout(request)
    return render(request, "sponsorship/declined.html")


# --- Admin review --------------------------------------------------------------------------


def review_queue(request):
    decision = can(request.user, "invitation.review_queue")
    if not decision:
        raise PermissionDenied(decision.reason)
    waiting_on_invitee = (
        Invitation.objects.filter(
            status__in=[Invitation.Status.ACCEPTED, Invitation.Status.WAITLISTED],
            invitee__identity__submitted_at__isnull=True,
        )
        .select_related("sponsor")
        .order_by("accepted_at")
    )
    guests = User.objects.filter(
        status=User.Status.GUEST,
        role_assignments__role__name=roles.GUEST,
        role_assignments__revoked_at__isnull=True,
    ).distinct().order_by("joined_at")
    from moderation.permanent import matches

    queue = list(onboarding.review_queue())
    for invitation in queue:
        # Rule 46: a match is shown to the reviewing Admin, never acted on automatically.
        invitation.permanent_ban_matches = matches(invitation.invitee_email, invitation.invitee.identity.real_name)
    return render(request, "sponsorship/review_queue.html", {
        "queue": queue,
        "waiting_on_invitee": waiting_on_invitee,
        "guests": guests,
    })


@require_POST
def review_approve(request, pk):
    onboarding.approve(request.user, get_object_or_404(Invitation, pk=pk))
    return redirect("review_queue")


@require_POST
def review_decline(request, pk):
    onboarding.decline(request.user, get_object_or_404(Invitation, pk=pk), reason=request.POST.get("reason", ""))
    return redirect("review_queue")


@require_POST
def comp_guest(request, user_pk):
    onboarding.comp(request.user, get_object_or_404(User, pk=user_pk))
    return redirect("review_queue")
