"""Build step 9: data export (rule 79, Privacy: Access requests)."""

import io
import json
import re
import zipfile
from datetime import timedelta

import pytest
from django.core.files.storage import default_storage
from django.test import Client
from django.utils import timezone

from accounts import data_rights
from accounts.models import IdentityRecord, User, UserSession
from audit.models import AuditEntry
from boards import messages, services
from boards.models import Attachment, ThreadParticipant
from core import jobs, watermark
from core.models import DataRequest, Notification
from core.services import set_site_setting
from moderation import reports as reporting
from moderation import services as moderation
from tests.factories import enrol_totp, make_dm, make_post, make_thread

ZW = re.compile("[⁠​]")


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def signed_in(user):
    enrol_totp(user)
    client = Client()
    client.force_login(user)
    return client


def msg(thread, author, text, ago=timedelta(0), **fields):
    from boards.models import Post

    return Post.objects.create(thread=thread, author=author, body_source=text, body_html=f"<p>{text}</p>",
                               created_at=timezone.now() - ago, **fields)


def built(member):
    item = data_rights.request_export(member)
    data_rights.build_exports()
    item.refresh_from_db()
    return item


def unzip(item):
    with default_storage.open(item.file_key, "rb") as handle:
        archive = zipfile.ZipFile(io.BytesIO(handle.read()))
    return {name: archive.read(name) for name in archive.namelist()}


def area(files, name):
    return json.loads(files[name])


# --- asking --------------------------------------------------------------------------------


def test_a_member_asks_from_settings_and_the_runner_builds_it(make_user, mailoutbox, django_capture_on_commit_callbacks):
    member = make_user("full")
    client = signed_in(member)
    assert b"Ask for an export" in client.get("/data/").content
    client.post("/data/", {"export": "1"})
    item = DataRequest.objects.get(user=member)
    session = UserSession.objects.get(user=member)
    assert item.kind == "export" and item.status == "open" and len(item.watermark_seed) == 8
    assert item.watermark_seed != session.watermark_seed  # the export's own (decided 5 Oct 2026)
    assert b"being prepared" in client.get("/data/").content
    with django_capture_on_commit_callbacks(execute=True):
        assert jobs.run_job(jobs.FREQUENT[0]) is True
    item.refresh_from_db()
    assert item.status == "ready" and default_storage.exists(item.file_key)
    assert Notification.objects.get(recipient=member, kind="data.export_ready")
    [mail] = mailoutbox
    assert mail.to == [member.email] and "export" not in mail.body.lower()  # a pointer, nothing more
    assert b"Download your export" in client.get("/data/").content
    actions = list(AuditEntry.objects.filter(target_type="core.datarequest").values_list("action", flat=True))
    assert actions == ["data_export.request", "data_export.build"]


@pytest.mark.parametrize("status", [User.Status.READ_ONLY, User.Status.SUSPENDED, User.Status.GUEST])
def test_any_member_who_can_sign_in_may_ask(make_user, status):
    member = make_user("full", status=status)
    assert data_rights.request_export(member).status == "open"


def test_one_export_per_period(make_user, owner):
    member = make_user("full")
    first = data_rights.request_export(member)
    with pytest.raises(Exception, match="another export on"):
        data_rights.request_export(member)
    DataRequest.objects.filter(pk=first.pk).update(requested_at=timezone.now() - timedelta(days=8))
    data_rights.request_export(member)
    set_site_setting(owner, "export.min_days_between", 30)
    DataRequest.objects.filter(user=member).update(requested_at=timezone.now() - timedelta(days=8))
    with pytest.raises(Exception, match="another export on"):
        data_rights.request_export(member)


def test_nobody_asks_for_someone_else(make_user):
    from django.core.exceptions import PermissionDenied

    from core.permissions import can

    member, other = make_user("full"), make_user("admin")
    assert not can(other, "data.export", member)
    with pytest.raises(PermissionDenied):
        data_rights.open_export_for(other, member)  # an Admin, and the member has not left


# --- contents --------------------------------------------------------------------------------


@pytest.fixture
def history(make_user, general, owner):
    """A member with a little of everything."""
    member, friend, admin, moderator = make_user("full"), make_user("full"), make_user("admin"), make_user("moderator")
    IdentityRecord.objects.create(user=member, real_name="Pat Example", phone="", vouching_notes="Sponsor's words here")
    thread, _ = services.start_thread(member, general, "My first thread", "Hello from the start")
    published = services.reply(member, thread, "A published reply")
    services.edit_post(member, published, "A published reply, edited")
    removed = services.reply(member, thread, "Something rude")
    services.delete_post(moderator, removed, "personal_attack")
    mine_deleted = services.reply(member, thread, "Changed my mind")
    services.delete_post(member, mine_deleted)
    redacted = services.reply(member, thread, "My address is 1 Secret Street")
    services.edit_post(admin, redacted, "My address is [redacted]", redact=True, redaction_reason="private_information")
    held = make_post(thread, member, is_held=True)
    other_thread = make_thread(general, friend)
    friend_post = make_post(other_thread, friend)
    reporting.report(member, friend_post, "spam", note="Looks like an advert")
    reporting.report(friend, published, "off_topic", note="Friend reported this")
    moderation.initiate_action(admin, member, "note", internal_reason="Staff-only note text")
    # Admins act alone, so the warning is in force at once.
    moderation.initiate_action(admin, member, "warning", internal_reason="Internal warning reason",
                               public_summary="Rude to another member")
    messages.block(member, make_user("full"))
    return {"member": member, "friend": friend, "held": held, "published": published, "redacted": redacted}


def test_posts_threads_and_versions(history):
    files = unzip(built(history["member"]))
    posts = {p["markdown"]: p for p in area(files, "posts.json")}
    assert posts["A published reply, edited"]["state"] == "published"
    assert [v["markdown"] for v in posts["A published reply, edited"]["earlier_versions"]] == [
        "A published reply", "A published reply, edited"]
    assert posts["Something rude"]["state"] == "removed by staff" and posts["Something rude"]["removal_reason"]
    assert posts["Changed my mind"]["state"] == "deleted by you"
    assert posts["text"]["state"] == "awaiting approval"
    redacted = posts["My address is [redacted]"]
    assert redacted["earlier_versions"] == []  # the text a redaction removed stays out
    assert b"1 Secret Street" not in b"".join(files.values())
    assert files[f"posts/{history['published'].pk}.md"].decode().endswith("A published reply, edited\n")
    assert [t["title"] for t in area(files, "threads.json")] == ["My first thread"]


def test_what_is_left_out(history):
    files = unzip(built(history["member"]))
    everything = b"".join(files.values()).decode()
    for secret in ("Sponsor's words here", "Staff-only note text", "Internal warning reason", "Friend reported this",
                   history["friend"].display_name):
        assert secret not in everything
    assert not any("audit" in name for name in files)
    assert area(files, "identity.json")["real_name"] == "Pat Example"
    [action] = area(files, "moderation.json")
    assert action == {**action, "kind": "warning", "summary": "Rude to another member", "status": "active"}
    assert [r["your_note"] for r in area(files, "reports.json")] == ["Looks like an advert"]
    assert len(area(files, "blocks.json")) == 1
    assert "README.txt" in files and "Not included" in files["README.txt"].decode()


def test_whole_conversations_for_the_time_they_were_in_them(make_user):
    member, other, third = make_user("full"), make_user("full"), make_user("full")
    thread = make_dm(other, third)
    now = timezone.now()
    msg(thread, other, "before they joined", timedelta(hours=5))
    ThreadParticipant.objects.create(thread=thread, user=member, joined_at=now - timedelta(hours=4),
                                     left_at=now - timedelta(hours=2))
    msg(thread, other, " ".join(f"w{i}" for i in range(30)), timedelta(hours=3))
    msg(thread, member, "my own words", timedelta(hours=3))
    msg(thread, third, "later deleted", timedelta(hours=3), deleted_at=now)
    msg(thread, other, "after they left", timedelta(hours=1))
    item = built(member)
    files = unzip(item)
    [conversation] = area(files, "messages.json")
    texts = [m.get("markdown") for m in conversation["messages"]]
    plain = watermark.strip(" ".join(t for t in texts if t))  # other members' words carry marks
    assert "before they joined" not in plain and "after they left" not in plain
    assert "my own words" in texts and None in texts  # the other member's deleted message has no text
    others = next(t for t in texts if t and t.startswith("w0"))
    [(seed, day)] = watermark.find(others)
    assert f"{seed:08x}" == item.watermark_seed
    assert not ZW.search("my own words")
    assert conversation["people"] == sorted([other.display_name, third.display_name])
    assert f"messages/{thread.pk}.md" in files
    assert "my own words" not in json.dumps(area(files, "posts.json"))  # DMs are not posts


def test_the_watermark_setting_turns_marks_off(make_user, owner):
    member, other = make_user("full"), make_user("full")
    thread = make_dm(member, other)
    msg(thread, other, " ".join(f"w{i}" for i in range(30)))
    set_site_setting(owner, "watermark.enabled", False)
    files = unzip(built(member))
    assert not ZW.search(files["messages.json"].decode())


def test_images_and_avatar_are_included(make_user, general):
    from django.core.files.base import ContentFile

    member = make_user("full")
    key = default_storage.save("attachments/test.png", ContentFile(b"\x89PNG fake"))
    Attachment.objects.create(post=None, uploader=member, storage_key=key, filename="me.png", mime="image/png",
                              size_bytes=9, width=1, height=1)
    files = unzip(built(member))
    [image] = area(files, "images.json")
    assert image["avatar"] and files[image["file"]] == b"\x89PNG fake"


def test_account_sessions_payments_and_sponsorship(make_user, client):
    from tests.factories import sponsor

    sponsor_user, member = make_user("tenured"), make_user("full")
    sponsor(sponsor_user, member)
    signed_in(member).get("/")
    files = unzip(built(member))
    account = area(files, "account.json")
    assert account["email"] == member.email and account["roles"][0]["role"] == "full"
    assert area(files, "sessions.json") and "session_key" not in files["sessions.json"].decode()
    assert area(files, "sponsorship.json")["your_sponsors"][0]["member"] == sponsor_user.display_name
    assert area(files, "payments.json")["payments"] == []


# --- downloading ------------------------------------------------------------------------------


def test_only_the_member_downloads_and_each_download_is_audited(make_user):
    member, other = make_user("full"), make_user("full")
    item = built(member)
    response = signed_in(member).get(f"/data/export/{item.pk}/download/")
    assert response.status_code == 200 and response["Content-Type"] == "application/zip"
    assert b"".join(response.streaming_content)[:2] == b"PK"
    assert signed_in(other).get(f"/data/export/{item.pk}/download/").status_code == 404
    assert AuditEntry.objects.filter(action="data_export.download", actor=member).count() == 1
    item.refresh_from_db()
    assert item.downloaded_at is not None


def test_files_are_deleted_after_the_keeping_period(make_user, django_capture_on_commit_callbacks):
    member = make_user("full")
    item = built(member)
    key = item.file_key
    assert data_rights.delete_old_exports(now=timezone.now() + timedelta(days=6)) == 0
    with django_capture_on_commit_callbacks(execute=True):
        assert data_rights.delete_old_exports(now=timezone.now() + timedelta(days=8)) == 1
    item.refresh_from_db()
    assert item.status == "done" and item.file_key == "" and not default_storage.exists(key)
    assert signed_in(member).get(f"/data/export/{item.pk}/download/").status_code == 404
    assert AuditEntry.objects.filter(action="data_export.delete_file").exists()


def test_the_runner_deletes_old_files_too(make_user, monkeypatch):
    item = built(make_user("full"))
    DataRequest.objects.filter(pk=item.pk).update(completed_at=timezone.now() - timedelta(days=8))
    jobs.run_job(jobs.FREQUENT[0])
    item.refresh_from_db()
    assert item.status == "done"


# --- a removed member --------------------------------------------------------------------------


@pytest.fixture
def former(make_user):
    member = make_user("full")
    User.objects.filter(pk=member.pk).update(status=User.Status.REMOVED, removed_at=timezone.now())
    member.refresh_from_db()
    return member


def test_an_owner_opens_an_export_and_the_link_works_once(former, owner, mailoutbox,
                                                          django_capture_on_commit_callbacks):
    client = signed_in(owner)
    client.post("/staff/data/", {"export_for": former.slug})
    item = DataRequest.objects.get(user=former)
    assert item.opened_by == owner
    with django_capture_on_commit_callbacks(execute=True):
        data_rights.build_exports()
    [mail] = mailoutbox
    assert mail.to == [former.email]
    link = re.search(r"https?://\S+(/data-export/\S+/)", mail.body).group(1)
    assert not Notification.objects.filter(recipient=former).exists()
    anonymous = Client()
    page = anonymous.get(link)
    assert page.status_code == 200 and b"Download" in page.content  # looking does not use it up
    assert anonymous.get(link).status_code == 200
    download = anonymous.post(link)
    assert download.status_code == 200 and b"".join(download.streaming_content)[:2] == b"PK"
    assert anonymous.post(link).status_code == 410
    assert anonymous.get(link).status_code == 410
    entries = list(AuditEntry.objects.filter(target_type="core.datarequest").values_list("action", "actor"))
    assert entries == [("data_export.open_for", owner.pk), ("data_export.build", None),
                       ("data_export.download", None)]


def test_the_emailed_link_expires(former, owner, mailoutbox, django_capture_on_commit_callbacks):
    data_rights.open_export_for(owner, former)
    with django_capture_on_commit_callbacks(execute=True):
        data_rights.build_exports()
    link = re.search(r"(/data-export/\S+/)", mailoutbox[0].body).group(1)
    DataRequest.objects.filter(user=former).update(link_expires_at=timezone.now() - timedelta(minutes=1))
    assert Client().post(link).status_code == 410
    assert Client().get("/data-export/not-a-real-token/").status_code == 410


def test_only_owners_open_exports_and_only_for_former_members(former, make_user, owner):
    admin, active = make_user("admin"), make_user("full")
    assert signed_in(admin).get("/staff/data/").status_code == 403
    response = signed_in(owner).post("/staff/data/", {"export_for": active.slug}, follow=True)
    assert b"only for a member whose membership ended" in response.content
    assert not DataRequest.objects.filter(user=active).exists()


def test_an_admin_cannot_open_one_even_for_a_former_member(former, make_user):
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        data_rights.open_export_for(make_user("admin"), former)


def test_owner_opened_exports_do_not_count_toward_the_members_limit(former, owner):
    data_rights.open_export_for(owner, former)
    assert data_rights.next_export_allowed(former) is None


# --- tracing --------------------------------------------------------------------------------


def test_the_tracing_page_finds_the_export_and_its_member(make_user, owner):
    member, other = make_user("full"), make_user("full")
    thread = make_dm(member, other)
    msg(thread, other, " ".join(f"w{i}" for i in range(30)))
    client = signed_in(member)
    client.get("/")
    item = built(member)
    leaked = area(unzip(item), "messages.json")[0]["messages"][0]["markdown"]
    UserSession.objects.filter(user=member).delete()
    page = signed_in(owner).post("/staff/trace/", {"excerpt": leaked}).content.decode()
    assert f"request {item.pk}" in page and member.display_name in page
    assert AuditEntry.objects.get(action="watermark.trace").payload["exports"] == [item.pk]


# --- pages ----------------------------------------------------------------------------------


def test_menu_links(make_user, owner):
    member_page = signed_in(make_user("full")).get("/").content.decode()
    assert 'href="/data/"' in member_page and 'href="/staff/data/"' not in member_page
    assert 'href="/staff/data/"' in signed_in(owner).get("/").content.decode()


def test_the_export_link_path_is_the_only_new_public_one(client, db):
    assert client.get("/data/").status_code == 302
    assert client.get("/staff/data/").status_code == 302
    assert client.get("/data-export/abc/").status_code == 410


def test_frequent_jobs(seeded):
    assert [job.name for job in jobs.FREQUENT] == ["data_requests"]


def test_revisions_written_by_staff_are_left_out(make_user, general):
    member, moderator = make_user("full"), make_user("moderator")
    thread = make_thread(general, member)
    post = services.reply(member, thread, "mine")
    services.edit_post(moderator, post, "staff tidy-up")
    posts = area(unzip(built(member)), "posts.json")
    assert [v["markdown"] for v in posts[0]["earlier_versions"]] == ["mine"]


def test_every_export_has_a_seed_of_its_own(make_user):
    first, second = make_user("full"), make_user("full")
    seeds = {data_rights.request_export(first).watermark_seed, data_rights.request_export(second).watermark_seed}
    assert len(seeds) == 2
