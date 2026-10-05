"""Data export (docs/DESIGN.md, Privacy: Access requests; rule 79).

A member asks from settings; an Owner opens the request for a removed member, who cannot sign in.
The job runner builds the zip in the background (build_exports, every five minutes): one JSON file
per area, the posts and messages also as their original Markdown, the member's images and a
README. Other members' messages carry the request's watermark. A signed-in member downloads from
their data page while the file is kept; a removed member gets one emailed link that works once,
without signing in, until it expires. Files are deleted after export.keep_days.
"""

import hashlib
import io
import json
import secrets
import zipfile
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from audit import log
from core import registry
from core.models import DataRequest, Notification
from core.permissions import can

Kind, Status = DataRequest.Kind, DataRequest.Status


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _require(actor, action, target):
    decision = can(actor, action, target)
    if not decision:
        raise PermissionDenied(decision.reason)


# --- asking --------------------------------------------------------------------------------


def next_export_allowed(user, now=None):
    """When the member may ask for another export, or None if they may now."""
    now = now or timezone.now()
    last = DataRequest.objects.filter(user=user, kind=Kind.EXPORT, opened_by__isnull=True).order_by(
        "-requested_at").first()
    if last is None:
        return None
    allowed = last.requested_at + timedelta(days=registry.site_value("export.min_days_between"))
    return allowed if allowed > now else None


@transaction.atomic
def request_export(user, session=None):
    """The member's own request. `session` is their UserSession, whose watermark seed marks other
    members' messages in the export."""
    _require(user, "data.export", user)
    allowed = next_export_allowed(user)
    if allowed:
        raise ValidationError(f"You can ask for another export on {timezone.localtime(allowed):%-d %B %Y}.")
    seed = session.watermark_seed if session is not None else secrets.token_hex(4)
    item = DataRequest.objects.create(user=user, kind=Kind.EXPORT, watermark_seed=seed)
    log.record(user, "data_export.request", item)
    return item


@transaction.atomic
def open_export_for(owner, member):
    """An Owner opens an export for a removed member, having confirmed who they are by email."""
    _require(owner, "data.open_for", member)
    item = DataRequest.objects.create(user=member, kind=Kind.EXPORT, opened_by=owner,
                                      watermark_seed=secrets.token_hex(4))
    log.record(owner, "data_export.open_for", item, {"member": member.pk})
    return item


# --- building ------------------------------------------------------------------------------


def _when(value):
    return value.isoformat() if value else None


def _name(user):
    return user.display_name if user else None


def _account(user):
    from allauth.account.models import EmailAddress
    from allauth.mfa.models import Authenticator

    from core.notifications import OPTIONAL_KINDS
    from core.models import NotificationPreference

    chosen = set(NotificationPreference.objects.filter(user=user, email=True).values_list("kind", flat=True))
    return {
        "display_name": user.display_name,
        "profile_address": user.slug,
        "email": user.email,
        "status": user.status,
        "joined_at": _when(user.joined_at),
        "last_seen_at": _when(user.last_seen_at),
        "membership_ended_at": _when(user.removed_at),
        "caption": user.caption,
        "emoji_display": user.emoji_display,
        "colour_scheme": user.colour_scheme,
        "email_addresses": [
            {"email": e.email, "verified": e.verified, "primary": e.primary}
            for e in EmailAddress.objects.filter(user=user).order_by("pk")
        ],
        "authenticator_app_set_up": Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).exists(),
        "roles": [
            {"role": a.role.name, "sub_forum": a.scope_subforum.name if a.scope_subforum else None,
             "granted_at": _when(a.granted_at), "revoked_at": _when(a.revoked_at)}
            for a in user.role_assignments.select_related("role", "scope_subforum").order_by("granted_at", "pk")
        ],
        "email_notifications_chosen": sorted(kind for kind in OPTIONAL_KINDS if kind in chosen),
    }


def _identity(user):
    from accounts.models import IdentityRecord

    record = IdentityRecord.objects.filter(user=user).first()
    if record is None:
        return None
    # The sponsor's vouching notes are the sponsor's words, and are left out (rule 79).
    return {
        "real_name": record.real_name,
        "phone": record.phone,
        "email_verified_at": _when(record.email_verified_at),
        "phone_verified_at": _when(record.phone_verified_at),
        "submitted_at": _when(record.submitted_at),
        "reviewed_at": _when(record.reviewed_at),
        "reduced_at": _when(record.reduced_at),
    }


def _post_state(post):
    if post.rejected_at:
        return "not approved"
    if post.is_held:
        return "awaiting approval"
    if post.deleted_at:
        return "deleted by you" if post.deleted_by_id == post.author_id else "removed by staff"
    return "published"


def _own_versions(post, user):
    """The member's own earlier versions of a post, leaving out any a staff redaction replaced."""
    revisions = list(post.revisions.order_by("edited_at", "pk"))
    last_redaction = max((r.edited_at for r in revisions if r.is_redaction), default=None)
    return [
        {"edited_at": _when(r.edited_at), "markdown": r.body_source}
        for r in revisions
        if r.edited_by_id == user.pk and r.body_source and not r.purged_at
        and (last_redaction is None or r.edited_at > last_redaction)
    ]


def _posts(user):
    from boards.models import Post, Thread

    posts = Post.objects.filter(author=user).exclude(thread__kind=Thread.Kind.DM).select_related(
        "thread__subforum").order_by("created_at", "pk")
    out = []
    for post in posts:
        out.append({
            "id": post.pk,
            "thread": post.thread_id,
            "thread_title": post.thread.title,
            "sub_forum": post.thread.subforum.name if post.thread.subforum else None,
            "created_at": _when(post.created_at),
            "edited_at": _when(post.edited_at),
            "state": _post_state(post),
            "removal_reason": post.delete_reason if post.deleted_at and post.deleted_by_id != user.pk else "",
            "markdown": post.body_source,
            "earlier_versions": _own_versions(post, user),
        })
    return out


def _threads(user):
    from boards.models import Thread

    out = []
    for thread in Thread.objects.filter(author=user).exclude(kind=Thread.Kind.DM).select_related(
            "subforum").order_by("created_at", "pk"):
        out.append({
            "id": thread.pk,
            "title": thread.title,
            "sub_forum": thread.subforum.name if thread.subforum else None,
            "created_at": _when(thread.created_at),
            "your_titles": [
                {"title": r.title, "edited_at": _when(r.edited_at)}
                for r in thread.title_revisions.filter(edited_by=user).order_by("edited_at", "pk")
            ],
        })
    return out


def _conversations(user, mark):
    """Every message in conversations the member took part in, for the time they were in them.
    Other members' messages carry `mark`; their deleted messages show only that they were deleted."""
    from boards.models import Post, ThreadParticipant

    periods = {}
    for p in ThreadParticipant.objects.filter(user=user, thread__kind="dm").select_related("thread").order_by(
            "joined_at"):
        periods.setdefault(p.thread, []).append(p)
    out = []
    for thread, spans in periods.items():
        window = None
        for span in spans:
            q = Post.objects.filter(thread=thread, created_at__gte=span.joined_at)
            if span.left_at:
                q = q.filter(created_at__lte=span.left_at)
            window = q if window is None else window | q
        messages = []
        for post in window.select_related("author").distinct().order_by("created_at", "pk"):
            own = post.author_id == user.pk
            entry = {"id": post.pk, "from": "you" if own else _name(post.author), "sent_at": _when(post.created_at),
                     "edited_at": _when(post.edited_at), "deleted": bool(post.deleted_at)}
            if own:
                entry["markdown"] = post.body_source
            elif not post.deleted_at:
                entry["markdown"] = mark(post.body_source)
            messages.append(entry)
        people = thread.participants.select_related("user").order_by("joined_at")
        out.append({
            "id": thread.pk,
            "subject": thread.title,
            "people": sorted({_name(p.user) for p in people if p.user_id != user.pk}),
            "your_time_in_it": [{"joined_at": _when(s.joined_at), "left_at": _when(s.left_at)} for s in spans],
            "messages": messages,
        })
    return out


def _images(user):
    from boards.models import Attachment

    return list(Attachment.objects.filter(uploader=user).order_by("created_at", "pk"))


def _sessions(user):
    from accounts.models import UserSession

    return [
        {"browser": s.browser_family, "system": s.os_family, "country": s.country, "network": s.ip_prefix,
         "started_at": _when(s.created_at), "last_used_at": _when(s.last_seen_at),
         "ended_at": _when(s.revoked_at), "ended_because": s.revoke_reason}
        for s in UserSession.objects.filter(user=user).order_by("created_at", "pk")
    ]


def _notifications(user):
    from core.notifications import describe

    return [
        {"what": describe(n)[0], "kind": n.kind, "created_at": _when(n.created_at), "read_at": _when(n.read_at),
         "emailed_at": _when(n.emailed_at)}
        for n in user.notifications.order_by("created_at", "pk")
    ]


def _payments(user):
    from billing.models import Charge, Entitlement, Gift, LapsePeriod, Subscription

    sub = Subscription.objects.filter(user=user).first()
    return {
        "membership": sub and {
            "status": sub.status, "paid_until": _when(sub.current_period_end),
            "renews": not sub.cancel_at_period_end, "comp_reason": sub.comp_reason or None,
            "comped_until": _when(sub.comped_until), "read_only_since": _when(sub.read_only_at),
        },
        "payments": [
            {"kind": c.kind, "amount": c.amount_cents / 100, "currency": c.currency, "status": c.status,
             "made_at": _when(c.created_at)}
            for c in Charge.objects.filter(user=user).order_by("created_at", "pk")
        ],
        "gifts_given": [{"to": _name(g.invitee), "charge": g.charge_id} for g in Gift.objects.filter(sponsor=user)
                        .select_related("invitee")],
        "gifts_received": [{"from": _name(g.sponsor)} for g in Gift.objects.filter(invitee=user)
                           .select_related("sponsor")],
        "extras": [
            {"extra": e.extra.name, "granted_at": _when(e.granted_at), "revoked_at": _when(e.revoked_at)}
            for e in Entitlement.objects.filter(user=user).select_related("extra").order_by("granted_at", "pk")
        ],
        "read_only_periods": [
            {"started_at": _when(p.started_at), "ended_at": _when(p.ended_at)}
            for p in LapsePeriod.objects.filter(user=user).order_by("started_at", "pk")
        ],
    }


def _sponsorship(user):
    from sponsorship.models import Sponsorship

    def rows(q, who):
        return [{"member": _name(getattr(s, who)), "from": _when(s.started_at), "until": _when(s.ended_at)}
                for s in q.select_related(who).order_by("started_at", "pk")]

    return {"your_sponsors": rows(Sponsorship.objects.filter(member=user), "sponsor"),
            "members_you_sponsored": rows(Sponsorship.objects.filter(sponsor=user), "member")}


def _moderation(user):
    from moderation.models import ModerationAction

    # Public summary, dates and status only: never internal reasons, notes or who reported.
    return [
        {"kind": a.kind, "summary": a.public_summary, "status": a.status, "starts_at": _when(a.starts_at),
         "ends_at": _when(a.ends_at), "recorded_at": _when(a.created_at)}
        for a in ModerationAction.objects.filter(target_user=user, is_public=True,
                                                 status__in=ModerationAction.RECORD_STATUSES)
        .order_by("created_at", "pk")
    ]


def _reports(user):
    from moderation.models import Report

    return [
        {"reason": r.get_reason_display() if r.reason else "", "your_note": r.note, "post": r.post_id,
         "filed_at": _when(r.created_at), "status": r.status, "outcome": r.outcome}
        for r in Report.objects.filter(reporter=user).order_by("created_at", "pk")
    ]


def _blocks(user):
    from accounts.models import Block

    return [{"member": _name(b.blocked), "since": _when(b.created_at)}
            for b in Block.objects.filter(blocker=user).select_related("blocked").order_by("created_at", "pk")]


README = """Your data from {site}
Built {built}.

account.json        Your profile and account: names, email addresses, roles, settings.
identity.json       The identity details you gave when you joined, if the forum still holds them.
posts.json          Every post you wrote outside direct messages, with its state (published,
                    awaiting approval, not approved, deleted, removed by staff) and your earlier
                    versions. The text of each is also in posts/, as you wrote it in Markdown.
threads.json        Threads you started and the titles you gave them.
messages.json       Every direct-message conversation you took part in, for the time you were in
                    it, including other members' messages. Each is also in messages/ as Markdown.
                    Messages other members wrote carry invisible marks that identify this export.
images/             Images you uploaded, and your avatar.
sessions.json       Where and when you were signed in.
notifications.json  Your notifications.
payments.json       Your membership, payments, gifts and extras.
sponsorship.json    Who sponsored you, and whom you sponsored.
moderation.json     Staff actions on your account, as shown on your public record.
reports.json        Reports you filed.
blocks.json         Members you blocked.

Not included: staff's internal reasons and notes, who reported you, the audit log, and the notes
your sponsor wrote when inviting you.
"""


def build_zip(item, now=None):
    from core import watermark

    user = item.user
    now = now or timezone.now()
    seed = watermark.seed_value(item.watermark_seed)
    if registry.site_value("watermark.enabled") and seed is not None:
        run = watermark.encode(seed, watermark.day_number(timezone.localdate(now)))

        def mark(text):
            return watermark.mark_html(text, run)
    else:
        def mark(text):
            return text

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        def put(name, data):
            archive.writestr(name, json.dumps(data, indent=2, ensure_ascii=False))

        put("account.json", _account(user))
        put("identity.json", _identity(user))
        posts = _posts(user)
        put("posts.json", posts)
        for post in posts:
            archive.writestr(f"posts/{post['id']}.md", f"# {post['thread_title']}\n\n{post['markdown']}\n")
        put("threads.json", _threads(user))
        conversations = _conversations(user, mark)
        put("messages.json", conversations)
        for c in conversations:
            lines = [f"# {c['subject']}", ""]
            for m in c["messages"]:
                body = m.get("markdown", "(deleted)" if m["deleted"] else "")
                lines += [f"## {m['from']}, {m['sent_at']}", "", body, ""]
            archive.writestr(f"messages/{c['id']}.md", "\n".join(lines))
        images = []
        for a in _images(user):
            name = f"images/{a.pk}-{a.filename or 'image'}"
            try:
                with default_storage.open(a.storage_key, "rb") as handle:
                    archive.writestr(name, handle.read())
            except (FileNotFoundError, OSError):
                continue
            images.append({"file": name, "post": a.post_id, "avatar": a.post_id is None,
                           "uploaded_at": _when(a.created_at)})
        put("images.json", images)
        put("sessions.json", _sessions(user))
        put("notifications.json", _notifications(user))
        put("payments.json", _payments(user))
        put("sponsorship.json", _sponsorship(user))
        put("moderation.json", _moderation(user))
        put("reports.json", _reports(user))
        put("blocks.json", _blocks(user))
        archive.writestr("README.txt", README.format(site=registry.site_value("site.name"),
                                                     built=f"{timezone.localtime(now):%-d %B %Y, %H:%M}"))
    return buffer.getvalue()


def _site_url(path):
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def build_exports(now=None):
    """The five-minute job: build every open export. Returns how many were built."""
    now = now or timezone.now()
    built = 0
    for pk in DataRequest.objects.filter(kind=Kind.EXPORT, status=Status.OPEN).values_list("pk", flat=True):
        with transaction.atomic():
            item = DataRequest.objects.select_for_update(of=("self",)).select_related("user").get(pk=pk)
            if item.status != Status.OPEN:
                continue
            item.status = Status.BUILDING
            data = build_zip(item, now)
            key = default_storage.save(f"exports/{item.user_id}/{secrets.token_hex(16)}.zip", ContentFile(data))
            item.file_key, item.status, item.completed_at = key, Status.READY, now
            token = None
            if item.opened_by_id:
                token = secrets.token_urlsafe(32)
                item.link_token_hash = _hash(token)
                item.link_expires_at = now + timedelta(hours=registry.site_value("export.link_hours"))
            item.save()
            log.record(None, "data_export.build", item, {"member": item.user_id})
            if token:
                email, url = item.user.email, _site_url(reverse("export_link", args=[token]))
                hours = registry.site_value("export.link_hours")
                transaction.on_commit(lambda email=email, url=url, hours=hours: send_mail(
                    "Your data export is ready",
                    f"The export of your data you asked for is ready. This link works once, for the next "
                    f"{hours} hours, and does not need you to sign in:\n\n{url}\n",
                    settings.DEFAULT_FROM_EMAIL, [email]))
            else:
                Notification.objects.create(recipient=item.user, kind="data.export_ready", payload={"request": item.pk})
            built += 1
    return built


def delete_old_exports(now=None):
    """Delete export files kept longer than export.keep_days. Returns how many were deleted."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=registry.site_value("export.keep_days"))
    deleted = 0
    for pk in DataRequest.objects.filter(kind=Kind.EXPORT, status=Status.READY, completed_at__lt=cutoff).values_list(
            "pk", flat=True):
        with transaction.atomic():
            item = DataRequest.objects.select_for_update().get(pk=pk)
            if item.status != Status.READY:
                continue
            key = item.file_key
            item.status, item.file_key, item.link_token_hash = Status.DONE, "", ""
            item.save(update_fields=["status", "file_key", "link_token_hash"])
            log.record(None, "data_export.delete_file", item)
            transaction.on_commit(lambda key=key: default_storage.delete(key))
            deleted += 1
    return deleted


# --- downloading ---------------------------------------------------------------------------


def downloadable(user):
    """The member's export that is ready, if any."""
    return DataRequest.objects.filter(user=user, kind=Kind.EXPORT, status=Status.READY,
                                      opened_by__isnull=True).order_by("-completed_at").first()


@transaction.atomic
def record_download(item, actor):
    item = DataRequest.objects.select_for_update().get(pk=item.pk)
    first = item.downloaded_at is None
    if first:
        item.downloaded_at = timezone.now()
        item.save(update_fields=["downloaded_at"])
    log.record(actor, "data_export.download", item, {"member": item.user_id, "by_link": actor is None})
    return item


def by_token(token, now=None):
    """The export an emailed link points to, while it is ready, unexpired and unused."""
    now = now or timezone.now()
    item = DataRequest.objects.filter(kind=Kind.EXPORT, link_token_hash=_hash(token)).first()
    if item is None or item.status != Status.READY or item.downloaded_at or not item.link_expires_at:
        return None
    return item if item.link_expires_at > now else None


@transaction.atomic
def use_token(token):
    """Spend an emailed link. Returns the request, or None if the link no longer works."""
    item = by_token(token)
    if item is None:
        return None
    item = DataRequest.objects.select_for_update().get(pk=item.pk)
    if item.downloaded_at:
        return None
    return record_download(item, None)
