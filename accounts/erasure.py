"""Erasure (docs/DESIGN.md, Privacy: Erasure requests; rule 80).

The member asks from their data page and confirms with an authenticator code; a removed member
asks by email and an Owner opens the request. Requests wait in a queue only Owners see. An Owner
runs one within erasure.deadline_days, or defers it once, for erasure.deferral_days, with a reason
the member sees; there is no refusal, and the member may withdraw until it runs. The Owner's Run
hands it to the job runner, which erases within five minutes.

Erasing performs the removal steps first, then turns the account into a tombstone named "Former
member <number>": personal fields cleared, sign-in details, sessions, read positions,
notifications, preferences, blocks made and identity details deleted, the invitation's email and
vouching notes cleared, the avatar deleted and the Stripe customer deleted. Posts and messages stay
under the tombstone; posts quoting or mentioning the member are re-rendered. Posts the member
listed are removed in full. It cannot be undone.
"""

import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from audit import log
from core import registry
from core.models import DataRequest, Notification
from core.permissions import can

Kind, Status = DataRequest.Kind, DataRequest.Status
WAITING = (Status.OPEN, Status.DEFERRED)
PLACEHOLDER = "Removed at the author's request"


def _require(actor, action, target=None):
    decision = can(actor, action, target)
    if not decision:
        raise PermissionDenied(decision.reason)


def deadline(item):
    if item.deferred_until:
        return item.deferred_until
    return item.requested_at + timedelta(days=registry.site_value("erasure.deadline_days"))


def waiting(user):
    return DataRequest.objects.filter(user=user, kind=Kind.ERASURE, status__in=WAITING + (Status.BUILDING,)).first()


_POST_ID = re.compile(r"/p/(\d+)/?|^\s*#?(\d+)\s*$", re.M)


def parse_posts(user, text):
    """Ids of the member's own posts from links (or bare numbers), one per line."""
    from boards.models import Post

    ids = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        match = _POST_ID.search(line)
        if not match:
            raise ValidationError(f"Not a link to a post: {line.strip()}")
        ids.append(int(match.group(1) or match.group(2)))
    ids = list(dict.fromkeys(ids))
    mine = set(Post.objects.filter(pk__in=ids, author=user).values_list("pk", flat=True))
    others = [i for i in ids if i not in mine]
    if others:
        raise ValidationError(f"These are not posts of yours: {', '.join(map(str, others))}.")
    return ids


def _notify_owners(kind, item):
    from accounts import roles

    for owner in roles.owners():
        Notification.objects.create(recipient=owner, kind=kind, payload={"request": item.pk})


def _check_code(user, code):
    from allauth.mfa.models import Authenticator
    from allauth.mfa.totp.internal.auth import TOTP

    authenticator = Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).first()
    if authenticator is None or not TOTP(authenticator).validate_code((code or "").strip()):
        raise ValidationError("That authenticator code is not right.")


@transaction.atomic
def request_erasure(user, code, posts_text=""):
    _require(user, "data.erasure", user)
    if waiting(user):
        raise ValidationError("You have already asked for erasure.")
    posts = parse_posts(user, posts_text)
    _check_code(user, code)
    item = DataRequest.objects.create(user=user, kind=Kind.ERASURE, confirmed_at=timezone.now(),
                                      posts_to_remove=posts)
    log.record(user, "data_erasure.request", item, {"posts": len(posts)})
    _notify_owners("data.erasure_requested", item)
    return item


@transaction.atomic
def open_erasure_for(owner, member, posts_text=""):
    """A removed member asks by email; the Owner confirms who they are through the address on file,
    which stands in for the authenticator step."""
    _require(owner, "data.open_for", member)
    if waiting(member):
        raise ValidationError("An erasure request for this member is already waiting.")
    posts = parse_posts(member, posts_text)
    item = DataRequest.objects.create(user=member, kind=Kind.ERASURE, opened_by=owner, confirmed_at=timezone.now(),
                                      posts_to_remove=posts)
    log.record(owner, "data_erasure.open_for", item, {"member": member.pk, "posts": len(posts)})
    _notify_owners("data.erasure_requested", item)
    return item


def _locked(item):
    return DataRequest.objects.select_for_update(of=("self",)).select_related("user").get(pk=item.pk)


@transaction.atomic
def withdraw(actor, item):
    item = _locked(item)
    _require(actor, "data.erasure.withdraw", item)
    item.status, item.completed_at = Status.WITHDRAWN, timezone.now()
    item.save(update_fields=["status", "completed_at"])
    log.record(actor, "data_erasure.withdraw", item, {"member": item.user_id})
    return item


@transaction.atomic
def defer(owner, item, reason):
    item = _locked(item)
    _require(owner, "data.erasure.defer", item)
    if not (reason or "").strip():
        raise ValidationError("Give the reason; the member sees it.")
    item.deferred_until = deadline(item) + timedelta(days=registry.site_value("erasure.deferral_days"))
    item.status, item.deferral_reason, item.handled_by = Status.DEFERRED, reason.strip(), owner
    item.save(update_fields=["deferred_until", "status", "deferral_reason", "handled_by"])
    log.record(owner, "data_erasure.defer", item, {"member": item.user_id})
    Notification.objects.create(recipient=item.user, kind="data.erasure_deferred",
                                payload={"request": item.pk, "reason": item.deferral_reason})
    return item


@transaction.atomic
def start(owner, item):
    """The Owner's Run. The job runner erases within five minutes."""
    item = _locked(item)
    _require(owner, "data.erasure.run", item)
    item.status, item.handled_by = Status.BUILDING, owner
    item.save(update_fields=["status", "handled_by"])
    log.record(owner, "data_erasure.start", item, {"member": item.user_id})
    return item


# --- erasing -------------------------------------------------------------------------------


def _new_number():
    while True:
        number = secrets.randbelow(900_000) + 100_000
        if not User.objects.filter(tombstone_number=number).exists():
            return number


def _remove_post(post, owner):
    """Full removal at the author's request: the row stays, its words and images go (rule 11)."""
    from boards.models import Attachment, PostRevision

    now = timezone.now()
    PostRevision.objects.filter(post=post).update(body_source="", purged_by=owner, purged_at=now)
    files = list(Attachment.objects.filter(post=post).values_list("storage_key", flat=True))
    Attachment.objects.filter(post=post).delete()
    post.body_source, post.body_html = "", f"<p>{PLACEHOLDER}</p>"
    if not post.deleted_at:
        post.deleted_at, post.deleted_by = now, owner
    post.delete_reason = PLACEHOLDER
    post.save(update_fields=["body_source", "body_html", "deleted_at", "deleted_by", "delete_reason"])
    return files  # posts quoting it are re-rendered with the rest of the member's, below


def _rerender_mentions(old_slug):
    """Posts that mentioned the member keep the typed text and stop linking. Nobody's words change."""
    from boards.models import Post
    from boards.services import _render

    pattern = rf"(^|[^\w@/.])@{re.escape(old_slug)}([^a-z0-9-]|$)"
    for post in Post.objects.filter(body_source__iregex=pattern).select_related("thread__subforum"):
        _render(post, validate_quotes=False, collect_mentions=False)


def _scrub_invitations(member):
    from sponsorship.models import Invitation

    ids = list(Invitation.objects.filter(invitee=member).values_list("pk", flat=True))
    Invitation.objects.filter(pk__in=ids).update(invitee_email="", vouching_notes="")
    # Notifications to the sponsor about this invitation once carried the address.
    for note in Notification.objects.filter(payload__invitation__in=ids, payload__has_key="invitee_email"):
        note.payload.pop("invitee_email", None)
        note.save(update_fields=["payload"])


def _delete_stripe_customer(member):
    from billing import stripe_api
    from billing.models import Subscription

    sub = Subscription.objects.select_for_update().filter(user=member).first()
    if sub is None or not sub.stripe_customer_id:
        return
    stripe_api.delete_customer(sub.stripe_customer_id)
    sub.stripe_customer_id, sub.stripe_subscription_id = "", ""
    sub.save(update_fields=["stripe_customer_id", "stripe_subscription_id"])


def erase(item):
    """Run one erasure the Owner started. Must run inside a transaction."""
    from allauth.account.models import EmailAddress
    from allauth.mfa.models import Authenticator
    from django.contrib.sessions.models import Session

    from accounts import removal
    from accounts.models import Block, IdentityRecord, UserSession
    from boards.models import Attachment, Post, PostQuote, ThreadRead
    from boards.services import rerender_quoting
    from core.models import NotificationPreference
    from sponsorship.models import Sponsorship

    item = _locked(item)
    if item.status != Status.BUILDING:
        return False
    owner = item.handled_by
    member = User.objects.select_for_update().get(pk=item.user_id)
    _require(owner, "data.erasure.run", item)
    if member.status not in (User.Status.REMOVED, User.Status.TOMBSTONE):
        removal.end_membership(member, owner, "", Sponsorship.EndReason.MEMBER_LEFT)
    address, old_slug = member.email, member.slug

    files = []
    for post in Post.objects.filter(pk__in=item.posts_to_remove, author=member):
        files += _remove_post(post, owner)

    for key in UserSession.objects.filter(user=member).values_list("session_key", flat=True):
        Session.objects.filter(session_key=key).delete()
    UserSession.objects.filter(user=member).delete()
    EmailAddress.objects.filter(user=member).delete()
    Authenticator.objects.filter(user=member).delete()
    ThreadRead.objects.filter(user=member).delete()
    Notification.objects.filter(recipient=member).delete()
    NotificationPreference.objects.filter(user=member).delete()
    Block.objects.filter(blocker=member).delete()
    IdentityRecord.objects.filter(user=member).delete()
    _scrub_invitations(member)
    _delete_stripe_customer(member)

    avatar = member.avatar
    number = _new_number()
    member.tombstone_number = number
    member.display_name = f"Former member {number}"
    member.slug = f"former-member-{number}"
    member.email = f"former-member-{number}@erased.invalid"
    member.caption, member.avatar, member.removal_reason, member.last_seen_at = "", None, "", None
    member.status = User.Status.TOMBSTONE
    member.set_unusable_password()
    member.save()
    if avatar is not None and avatar.post_id is None:
        files.append(avatar.storage_key)
        Attachment.objects.filter(pk=avatar.pk).delete()

    # Quotes of the member's posts carry the author's name; mentions linked to the old address.
    for post in Post.objects.filter(pk__in=PostQuote.objects.filter(quoted_post__author=member)
                                    .values("quoted_post")):
        rerender_quoting(post)
    _rerender_mentions(old_slug)

    item.status, item.completed_at = Status.DONE, timezone.now()
    item.posts_to_remove = []
    item.save(update_fields=["status", "completed_at", "posts_to_remove"])
    log.record(owner, "data_erasure.run", item, {"member": member.pk})

    def after():
        for key in files:
            if key:
                default_storage.delete(key)
        send_mail("Your data has been erased",
                  "As you asked, your account on the forum has been erased. Your posts stay, under a name that "
                  "is no longer yours. This is the last email we will send to this address.\n",
                  settings.DEFAULT_FROM_EMAIL, [address])

    transaction.on_commit(after)
    return True


def run_erasures():
    """The five-minute job: run every erasure an Owner started. Returns how many ran."""
    done = 0
    for pk in DataRequest.objects.filter(kind=Kind.ERASURE, status=Status.BUILDING).values_list("pk", flat=True):
        with transaction.atomic():
            done += bool(erase(DataRequest(pk=pk)))
    return done


def remind_owners(now=None):
    """Once per request, when its deadline is erasure.reminder_days away (or past) and it still waits."""
    now = now or timezone.now()
    sent = 0
    for item in DataRequest.objects.filter(kind=Kind.ERASURE, status__in=WAITING):
        if deadline(item) - now > timedelta(days=registry.site_value("erasure.reminder_days")):
            continue
        if Notification.objects.filter(kind="data.erasure_due_soon", payload__request=item.pk).exists():
            continue
        with transaction.atomic():
            _notify_owners("data.erasure_due_soon", item)
        sent += 1
    return sent
