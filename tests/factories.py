"""Plain helpers for building test data directly, bypassing services and permission checks."""

from datetime import timedelta

from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.utils import timezone

from accounts.models import Role, RoleAssignment
from boards.models import Post, Thread, ThreadParticipant
from sponsorship.models import Sponsorship


def grant(user, role_name, scope_subforum=None, granted_at=None):
    return RoleAssignment.objects.create(
        user=user,
        role=Role.objects.get(name=role_name),
        scope_subforum=scope_subforum,
        granted_at=granted_at or timezone.now(),
    )


def sponsor(sponsor_user, member):
    return Sponsorship.objects.create(
        sponsor=sponsor_user, member=member, sponsor_role=sponsor_user.role_assignments.order_by("-role__rank")[0].role
    )


def make_thread(subforum, author, title="A thread", created_at=None):
    return Thread.objects.create(
        subforum=subforum, kind=Thread.Kind.DISCUSSION, title=title, author=author,
        created_at=created_at or timezone.now(),
    )


def make_post(thread, author, ago=None, **fields):
    """A post written `ago` before now (a timedelta), bypassing the services."""
    created = timezone.now() - (ago or timedelta(0))
    return Post.objects.create(
        thread=thread, author=author, body_source="text", body_html="<p>text</p>", created_at=created, **fields
    )


def make_dm(*participants):
    thread = Thread.objects.create(kind=Thread.Kind.DM, title="DM", author=participants[0])
    for user in participants:
        ThreadParticipant.objects.create(thread=thread, user=user)
    return thread


def enrol_totp(user):
    return TOTP.activate(user, generate_totp_secret())
