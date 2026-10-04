"""Notifications (docs/DESIGN.md, Notifications; rule 39).

Every notification is in-app. Email is only a pointer: it never carries post text, DM text or who
wrote, only that something is waiting.

- Account kinds always email, as soon as they happen: invitation and onboarding steps, approval or
  decline, and actions taken on the member's account. (Billing problems join them in build step 5.)
- Optional kinds email only if the member turns them on, and are combined into at most
  notifications.max_emails_per_day emails a day by the send_notification_emails job.
- Every other kind (staff notices, report outcomes, hidden posts) is in-app only.
"""

from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

ACCOUNT_KINDS = {
    "invitation.accepted", "invitation.invitee_declined", "invitation.approved", "invitation.declined",
    "moderation.action", "moderation.ban_lifted",
    # Billing problems and changes (docs/DESIGN.md, Billing emails).
    "billing.renewal_reminder", "billing.payment_failed", "billing.read_only_soon", "billing.read_only",
    "billing.restored", "billing.founding_comp_ending",
    # Sponsorship transfer: the member's account is read-only until it ends (rule 51).
    "sponsorship.transfer_opened", "sponsorship.offer", "sponsorship.transfer_ended",
    # Session binding (rule 58).
    "session.concurrent_location",
}
OPTIONAL_KINDS = {
    "dm": "New direct messages",
    "mention": "Mentions of you",
    "thread.reply": "Replies in threads you follow",
    "promotion": "Promotion news",
}


def describe(notification):
    """One line for the notifications page, and the address it links to."""
    kind, data = notification.kind, notification.payload
    post = data.get("post")
    lines = {
        "mention": ("You were mentioned in a post", post and reverse("post_link", args=[post])),
        "dm": ("New direct message", data.get("thread") and reverse("conversation", args=[data["thread"]])),
        "thread.reply": ("New reply in a thread you follow", post and reverse("post_link", args=[post])),
        "promotion": ("News about your promotion", None),
        "billing.gift": ("Your sponsor has paid for your first year", reverse("billing")),
        "post.rejected": ("A post of yours was not approved", None),
        "post.hidden": (f"A post of yours was removed: {data.get('reason', '')}", None),
        "moderation.action": ("Staff took an action on your account", None),
        "moderation.ban_lifted": ("Your ban has been lifted", None),
        "moderation.declined": (f"Your proposed action was declined: {data.get('reason', '')}", reverse("queue")),
        "moderation.note": ("A staff note was added", reverse("feed")),
        "queue.escalated": ("An item was escalated to Admins", reverse("queue")),
        "queue.escalation_resolved": ("An item you escalated was resolved", reverse("feed")),
        "report.outcome": ("Your report: " + {"action_taken": "action taken",
                                              "no_action": "no action needed"}.get(data.get("outcome"), ""), None),
        "billing.renewal_reminder": ("Your membership renews soon", reverse("billing")),
        "billing.payment_failed": ("A membership payment failed", reverse("billing")),
        "billing.read_only_soon": ("Your account becomes read-only in a few days", reverse("billing")),
        "billing.read_only": ("Your account is read-only until you renew", reverse("billing")),
        "billing.restored": ("Your membership is active again", reverse("billing")),
        "billing.founding_comp_ending": ("Your founding membership ends soon", reverse("billing")),
        "invitation.accepted": ("Someone accepted your invitation", reverse("invitations")),
        "invitation.invitee_declined": ("Someone declined your invitation", reverse("invitations")),
        "invitation.approved": ("Your invitee was approved", reverse("invitations")),
        "invitation.declined": ("Your invitee was not approved", reverse("invitations")),
        "sponsorship.transfer_opened": ("You need a new sponsor", reverse("transfer_status")),
        "sponsorship.offer": ("Someone has offered to vouch for you", reverse("transfer_status")),
        "sponsorship.transfer_ended": ({"resumed": "Your sponsor is back; your account is no longer read-only",
                                        "admin_sponsored": "Staff have become your sponsor"}.get(
                                           data.get("outcome"), "You have a sponsor again"), None),
        "sponsorship.offer_accepted": ("Your offer to vouch was accepted; you are now their sponsor",
                                       reverse("invitations")),
        "sponsorship.offer_declined": ("Your offer to vouch was declined", reverse("invitations")),
        "session.concurrent_location": ("Your account was in use from two countries at the same time, so we asked "
                                        "the newer device to sign in again. If that wasn't you, change your password.",
                                        reverse("sessions")),
    }
    return lines.get(kind, ("Something new", None))


def _pointer_email(user, account):
    url = f"{settings.SITE_URL.rstrip('/')}{reverse('notifications')}"
    if account:
        subject, body = "News about your forum account", f"There is news about your account. Sign in to read it:\n\n{url}\n"
    else:
        subject, body = "Something is waiting for you on the forum", f"Sign in to see what is new:\n\n{url}\n"
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])


def on_created(notification):
    """Account kinds email straight away, once the creating transaction commits."""
    if notification.kind not in ACCOUNT_KINDS:
        return
    from core.models import Notification

    def send():
        _pointer_email(notification.recipient, account=True)
        Notification.objects.filter(pk=notification.pk).update(emailed_at=timezone.now())

    transaction.on_commit(send)


def email_wanted(user, kind):
    from core.models import NotificationPreference

    return kind in OPTIONAL_KINDS and NotificationPreference.objects.filter(user=user, kind=kind, email=True).exists()


def send_digests(now=None):
    """The daily job: one pointer email per member with unread, un-emailed notifications of kinds
    they chose, within notifications.max_emails_per_day. Returns how many emails went out."""
    from accounts.models import User
    from core import registry
    from core.models import Notification, NotificationPreference

    now = now or timezone.now()
    limit = registry.site_value("notifications.max_emails_per_day")
    sent = 0
    for user in User.objects.filter(notification_preferences__email=True).distinct():
        kinds = list(NotificationPreference.objects.filter(user=user, email=True, kind__in=OPTIONAL_KINDS)
                     .values_list("kind", flat=True))
        waiting = Notification.objects.filter(recipient=user, kind__in=kinds, read_at__isnull=True, emailed_at__isnull=True)
        if not waiting.exists():
            continue
        recent = Notification.objects.filter(
            recipient=user, kind__in=OPTIONAL_KINDS, emailed_at__gt=now - timedelta(days=1)
        ).values("emailed_at").distinct().count()
        if recent >= limit:
            continue
        _pointer_email(user, account=False)
        waiting.update(emailed_at=now)
        sent += 1
    return sent
