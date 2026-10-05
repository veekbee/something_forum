"""Build step 9: erasure (rule 80, Privacy: Erasure requests)."""

from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client
from django.utils import timezone

from accounts import erasure
from accounts.models import Block, IdentityRecord, User, UserSession
from audit.models import AuditEntry
from billing.models import Charge, Subscription
from boards import messages, services
from boards.models import Attachment, Post, PostRevision, ThreadRead
from core import jobs
from core.models import DataRequest, Notification, NotificationPreference
from moderation import permanent
from moderation.models import PermanentBanRecord
from sponsorship.models import Invitation, Sponsorship, SponsorshipTransfer
from tests.factories import accepted, grant, make_dm, make_post, make_thread, sponsor

SECRETS = {}


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture(autouse=True)
def stripe(monkeypatch):
    calls = []
    monkeypatch.setattr("billing.stripe_api.delete_customer", lambda cid: calls.append(("delete", cid)))
    monkeypatch.setattr("billing.stripe_api.cancel_subscription", lambda sid: calls.append(("cancel", sid)))
    return calls


def enrol(user):
    secret = generate_totp_secret()
    TOTP.activate(user, secret)
    SECRETS[user.pk] = secret
    return user


def code(user):
    import time

    from allauth.mfa import app_settings

    return format_hotp_value(hotp_value(SECRETS[user.pk], int(time.time()) // app_settings.TOTP_PERIOD))


def signed_in(user):
    if user.pk not in SECRETS:
        enrol(user)
    client = Client()
    client.force_login(user)
    return client


def asked(member, posts=""):
    from django.core.cache import cache

    if member.pk not in SECRETS:
        enrol(member)
    cache.clear()  # a code is accepted once (replay protection); these tests reuse the moment
    return erasure.request_erasure(member, code(member), posts)


def erased(member, owner, posts=""):
    item = asked(member, posts)
    erasure.start(owner, item)
    assert erasure.run_erasures() == 1
    member.refresh_from_db()
    item.refresh_from_db()
    return item


# --- asking --------------------------------------------------------------------------------


def test_a_member_asks_with_an_authenticator_code(make_user, owner, mailoutbox, django_capture_on_commit_callbacks):
    member = make_user("full")
    client = signed_in(member)
    assert b"Ask for erasure" in client.get("/data/").content
    client.post("/data/", {"erase": "1", "code": "000000"})
    assert not DataRequest.objects.filter(user=member).exists()
    with django_capture_on_commit_callbacks(execute=True):
        client.post("/data/", {"erase": "1", "code": code(member)})
    item = DataRequest.objects.get(user=member, kind="erasure")
    assert item.status == "open" and item.confirmed_at and item.opened_by is None
    assert Notification.objects.get(recipient=owner, kind="data.erasure_requested")
    assert [m.to for m in mailoutbox] == [[owner.email]]  # Owners get a pointer email
    page = client.get("/data/").content.decode()
    assert "Withdraw the request" in page and "An Owner will run it by" in page
    assert AuditEntry.objects.filter(action="data_erasure.request", actor=member).exists()


def test_one_request_at_a_time(make_user):
    member = make_user("full")
    asked(member)
    with pytest.raises(ValidationError, match="already asked"):
        asked(member)


@pytest.mark.parametrize("role", ["moderator", "admin", "owner"])
def test_staff_give_up_their_role_first(make_user, role):
    member = enrol(make_user(role))
    with pytest.raises(PermissionDenied, match="staff role"):
        erasure.request_erasure(member, code(member))
    assert b"Give up your staff role first" in signed_in(member).get("/data/").content


def test_the_last_owner_cannot_be_erased(owner):
    enrol(owner)
    with pytest.raises(PermissionDenied):
        erasure.request_erasure(owner, code(owner))


def test_listing_posts_to_remove(make_user, general):
    member, other = make_user("full"), make_user("full")
    thread = make_thread(general, member)
    mine, theirs = make_post(thread, member), make_post(thread, other)
    with pytest.raises(ValidationError, match="not posts of yours"):
        asked(member, f"https://forum.example/p/{theirs.pk}/")
    with pytest.raises(ValidationError, match="Not a link"):
        asked(member, "the one about cats")
    item = asked(member, f"https://forum.example/p/{mine.pk}/\n\n{mine.pk}\n")
    assert item.posts_to_remove == [mine.pk]


def test_owners_do_not_withdraw_a_members_own_request(make_user, owner):
    item = asked(make_user("full"))
    with pytest.raises(PermissionDenied):
        erasure.withdraw(owner, item)


def test_withdrawing_until_it_runs(make_user, owner):
    member = make_user("full")
    item = asked(member)
    signed_in(member).post("/data/", {"withdraw": "1"})
    item.refresh_from_db()
    assert item.status == "withdrawn"
    item = asked(member)
    erasure.start(owner, item)
    with pytest.raises(PermissionDenied):
        erasure.withdraw(member, item)


# --- the Owners' queue ---------------------------------------------------------------------


def test_owners_defer_once_with_a_reason_the_member_sees(make_user, owner):
    member = make_user("full")
    item = asked(member)
    first_deadline = erasure.deadline(item)
    with pytest.raises(ValidationError):
        erasure.defer(owner, item, "")
    erasure.defer(owner, item, "An appeal is in progress")
    item.refresh_from_db()
    assert item.status == "deferred" and erasure.deadline(item) == first_deadline + timedelta(days=30)
    assert Notification.objects.get(recipient=member, kind="data.erasure_deferred").payload["reason"] == \
        "An appeal is in progress"
    assert "An appeal is in progress" in signed_in(member).get("/data/").content.decode()
    with pytest.raises(PermissionDenied):
        erasure.defer(owner, item, "Again")


def test_there_is_no_refusal(seeded):
    from core import permissions

    assert not any("refuse" in name or "reject" in name for name in permissions._RULES if name.startswith("data."))


def test_only_owners_run_or_defer(make_user, owner):
    admin, member = make_user("admin"), make_user("full")
    item = asked(member)
    for act in (lambda: erasure.start(admin, item), lambda: erasure.defer(admin, item, "No")):
        with pytest.raises(PermissionDenied):
            act()
    assert signed_in(admin).get("/staff/data/").status_code == 403


def test_the_queue_page_runs_with_a_confirmation(make_user, owner):
    member = make_user("full")
    item = asked(member)
    client = signed_in(owner)
    page = client.get("/staff/data/").content.decode()
    assert member.display_name in page and "Run erasure" in page
    client.post("/staff/data/", {"request": item.pk, "action": "run"})
    item.refresh_from_db()
    assert item.status == "open"  # not without ticking the box
    client.post("/staff/data/", {"request": item.pk, "action": "run", "confirm": "erase"})
    item.refresh_from_db()
    assert item.status == "building" and item.handled_by == owner
    jobs.run_job(jobs.FREQUENT[0])
    item.refresh_from_db()
    assert item.status == "done"


def test_a_removed_member_with_a_staff_role_waits_for_it_to_go(make_user, owner):
    former = make_user("moderator")
    User.objects.filter(pk=former.pk).update(status=User.Status.REMOVED, removed_at=timezone.now())
    item = erasure.open_erasure_for(owner, User.objects.get(pk=former.pk))
    with pytest.raises(PermissionDenied, match="staff role"):
        erasure.start(owner, item)


def test_owners_are_reminded_once_a_week_before_the_deadline(make_user, owner):
    item = asked(make_user("full"))
    assert erasure.remind_owners() == 0
    later = timezone.now() + timedelta(days=24)
    assert erasure.remind_owners(now=later) == 1
    assert erasure.remind_owners(now=later) == 0
    assert Notification.objects.get(kind="data.erasure_due_soon").payload == {"request": item.pk}


def test_an_owner_opens_one_for_a_former_member(make_user, owner):
    former = make_user("full")
    User.objects.filter(pk=former.pk).update(status=User.Status.REMOVED, removed_at=timezone.now())
    signed_in(owner).post("/staff/data/", {"erase_for": former.slug})
    item = DataRequest.objects.get(user=former, kind="erasure")
    assert item.opened_by == owner and item.confirmed_at
    erasure.withdraw(owner, item)  # Owners withdraw for them, if asked
    with pytest.raises(PermissionDenied):
        erasure.open_erasure_for(owner, make_user("full"))  # only for a membership that ended


# --- what erasure does ----------------------------------------------------------------------


@pytest.fixture
def life(make_user, general, owner):
    """A member with a sponsee, a subscription, posts, quotes, mentions, DMs and more."""
    sponsor_user = make_user("tenured")
    invitation = accepted(sponsor_user, totp=False, submit=False)
    member = invitation.invitee
    User.objects.filter(pk=member.pk).update(status=User.Status.ACTIVE)
    member.refresh_from_db()
    grant(member, "full")
    sponsor(sponsor_user, member)
    old = {"email": member.email, "slug": member.slug, "name": member.display_name}
    sponsee = make_user("provisional")
    sponsor(member, sponsee)
    enrol(member)
    UserSession.objects.create(user=member, session_key="k1", device_id="d", watermark_seed="ab")
    Subscription.objects.create(user=member, stripe_customer_id="cus_1", stripe_subscription_id="sub_1",
                                status="active")
    Charge.objects.create(user=member, kind="subscription", stripe_payment_intent_id="pi_1", amount_cents=5000)
    avatar_key = default_storage.save("avatars/me.png", ContentFile(b"png"))
    avatar = Attachment.objects.create(uploader=member, storage_key=avatar_key, filename="me.png", mime="image/png",
                                       size_bytes=3, width=1, height=1)
    User.objects.filter(pk=member.pk).update(avatar=avatar, caption="Hello there")
    other = make_user("full")
    thread = make_thread(general, other)
    kept = services.reply(member, thread, "A thought worth keeping")
    services.edit_post(member, kept, "A thought worth keeping, edited")
    regret = services.reply(member, thread, "Something I regret")
    services.edit_post(member, regret, "Something I regret, said twice")
    image_key = default_storage.save("attachments/r.png", ContentFile(b"img"))
    Attachment.objects.create(post=regret, uploader=member, storage_key=image_key, filename="r.png", mime="image/png",
                              size_bytes=3, width=1, height=1)
    quoting = services.reply(other, thread, f"[quote={kept.pk}]\nA thought\n[/quote]\n\nAgreed.")
    quoting_regret = services.reply(other, thread, f"[quote={regret.pk}]\nregret\n[/quote]\n\nOh.")
    mention = services.reply(other, thread, f"Thanks @{old['slug']} for this")
    dm = make_dm(member, other)
    dm_message = make_post(dm, member)
    ThreadRead.objects.create(user=member, thread=thread, last_read_at=timezone.now())
    NotificationPreference.objects.create(user=member, kind="mention", email=True)
    Notification.objects.create(recipient=member, kind="mention", payload={})
    messages.block(member, make_user("full"))
    messages.block(other, member)
    member.refresh_from_db()
    return {"member": member, "old": old, "sponsee": sponsee, "other": other, "kept": kept, "regret": regret,
            "quoting": quoting, "quoting_regret": quoting_regret, "mention": mention, "dm_message": dm_message,
            "invitation": invitation, "avatar_key": avatar_key, "image_key": image_key, "sponsor": sponsor_user}


def test_the_account_becomes_a_tombstone(life, owner, stripe, mailoutbox, django_capture_on_commit_callbacks):
    member, old = life["member"], life["old"]
    with django_capture_on_commit_callbacks(execute=True):
        item = erased(member, owner, f"/p/{life['regret'].pk}/")
    assert member.status == "tombstone" and member.tombstone_number
    assert member.display_name == f"Former member {member.tombstone_number}"
    assert member.slug == f"former-member-{member.tombstone_number}"
    assert member.email.endswith("@erased.invalid") and old["email"] not in member.email
    assert member.caption == "" and member.avatar is None and not member.has_usable_password()
    assert not member.is_active
    for model, field in ((EmailAddress, "user"), (Authenticator, "user"), (UserSession, "user"),
                         (ThreadRead, "user"), (Notification, "recipient"), (NotificationPreference, "user"),
                         (IdentityRecord, "user")):
        assert not model.objects.filter(**{field: member}).exists(), model
    assert not Block.objects.filter(blocker=member).exists()
    assert Block.objects.filter(blocked=member).exists()  # stays, but does nothing
    invitation = Invitation.objects.get(pk=life["invitation"].pk)
    assert invitation.invitee_email == "" and invitation.vouching_notes == ""
    assert ("cancel", "sub_1") in stripe and ("delete", "cus_1") in stripe
    sub = Subscription.objects.get(user=member)
    assert sub.stripe_customer_id == "" and sub.stripe_subscription_id == ""
    assert Charge.objects.filter(user=member).count() == 1
    assert not default_storage.exists(life["avatar_key"])
    assert item.status == "done" and item.posts_to_remove == []
    entry = AuditEntry.objects.get(action="data_erasure.run")
    assert entry.payload == {"member": member.pk} and entry.actor == owner
    assert [m.to for m in mailoutbox if m.subject == "Your data has been erased"] == [[old["email"]]]


def test_membership_ends_first_and_sponsees_open_transfers(life, owner):
    erased(life["member"], owner)
    assert life["member"].removed_at is not None
    assert SponsorshipTransfer.objects.filter(member=life["sponsee"], status="open").exists()
    assert not Sponsorship.objects.filter(member=life["member"], ended_at__isnull=True).exists()
    # The pedigree keeps the tombstone node, with only the sponsor link and dates.
    link = Sponsorship.objects.get(member=life["member"])
    assert link.sponsor == life["sponsor"] and link.started_at


def test_posts_stay_under_the_tombstone_and_others_render_the_new_name(life, owner):
    member, old = life["member"], life["old"]
    erased(member, owner)
    kept = Post.objects.get(pk=life["kept"].pk)
    assert kept.author == member and kept.body_source == "A thought worth keeping, edited"
    assert PostRevision.objects.filter(post=kept).count() == 2
    assert Post.objects.get(pk=life["dm_message"].pk).author == member
    quoting = Post.objects.get(pk=life["quoting"].pk)
    assert member.display_name in quoting.body_html and old["name"] not in quoting.body_html
    mention = Post.objects.get(pk=life["mention"].pk)
    assert f"@{old['slug']}" in mention.body_source  # nobody else's words change
    assert f"/m/{old['slug']}" not in mention.body_html and 'class="mention"' not in mention.body_html


def test_listed_posts_are_removed_in_full(life, owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        erased(life["member"], owner, f"/p/{life['regret'].pk}/")
    regret = Post.objects.get(pk=life["regret"].pk)
    assert regret.body_source == "" and "Something I regret" not in regret.body_html
    assert regret.delete_reason == erasure.PLACEHOLDER and regret.deleted_at
    assert not PostRevision.objects.filter(post=regret).exclude(body_source="").exists()
    assert not Attachment.objects.filter(post=regret).exists() and not default_storage.exists(life["image_key"])
    quoting = Post.objects.get(pk=life["quoting_regret"].pk)
    assert "Quoted post deleted" in quoting.body_html and "Oh." in quoting.body_html
    assert Post.objects.filter(pk=life["quoting_regret"].pk).exists()  # the replies stay


def test_the_tombstone_profile_shows_only_the_label(life, owner, make_user):
    member = life["member"]
    erased(member, owner)
    client = signed_in(make_user("full"))
    page = client.get(f"/members/{member.slug}/").content.decode()
    assert member.display_name in page and "Membership ended" in page
    assert "Rap Sheet" not in page and "joined" not in page
    assert client.get(f"/members/{member.slug}/rap-sheet/").status_code == 404
    assert client.get(f"/members/{life['old']['slug']}/").status_code == 404


def test_it_cannot_be_undone(life, owner):
    from accounts import removal

    member = life["member"]
    item = erased(member, owner)
    with pytest.raises(PermissionDenied):
        removal.reinstate(owner, member, "Changed their mind")
    item.status = "building"
    assert not erasure.erase(DataRequest(pk=item.pk))  # already done: nothing runs twice


def test_the_permanent_ban_list_survives(make_user, owner):
    member = make_user("full")
    action = permanent.impose(owner, member, "repeated harassment", "Harassment")
    record = PermanentBanRecord.objects.get(action=action)
    emails = record.emails
    User.objects.filter(pk=member.pk).update(status=User.Status.REMOVED, removed_at=timezone.now())
    item = erasure.open_erasure_for(owner, User.objects.get(pk=member.pk))
    erasure.start(owner, item)
    erasure.run_erasures()
    record.refresh_from_db()
    assert record.emails == emails and record.real_name is not None


def test_retention_counts_an_erased_member_as_left(life, owner, database_roles):
    from audit import retention

    member = life["member"]
    erased(member, owner)
    entry = AuditEntry.objects.filter(action="data_erasure.request").get()
    User.objects.filter(pk=member.pk).update(removed_at=timezone.now() - timedelta(days=3 * 365))
    retention.run()
    assert not AuditEntry.objects.filter(pk=entry.pk).exists()
    assert DataRequest.objects.filter(user=member, kind="erasure").exists()  # the row survives
