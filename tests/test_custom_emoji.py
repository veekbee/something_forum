"""Custom emoji (rule 62)."""

import io
import itertools

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from billing import stripe_api, webhooks
from billing.models import Charge, Extra
from boards import emoji, services
from boards.models import CustomEmoji
from core.permissions import can
from core.services import set_site_setting
from moderation import queue
from tests.factories import enrol_totp, make_thread

_ids = itertools.count(1)


def pay(user):
    webhooks.process({"id": f"evt_emoji_{next(_ids)}", "type": "checkout.session.completed", "data": {"object": {
        "id": f"cs_emoji_{next(_ids)}", "mode": "payment", "payment_status": "paid", "amount_total": 1000,
        "currency": "usd", "payment_intent": f"pi_emoji_{next(_ids)}",
        "metadata": {"purpose": "extra", "user": str(user.pk), "extra": "custom_emoji"},
    }}})


def png(width=64, height=64):
    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), (200, 40, 40, 255)).save(buffer, "PNG")
    return SimpleUploadedFile("e.png", buffer.getvalue())


def gif(frames=3):
    buffer = io.BytesIO()
    images = [Image.new("RGB", (32, 32), (i * 60, 0, 0)) for i in range(frames)]
    images[0].save(buffer, "GIF", save_all=True, append_images=images[1:], duration=100, loop=0)
    return SimpleUploadedFile("e.gif", buffer.getvalue())


def signed_in(user):
    enrol_totp(user)
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def member(make_user):
    return make_user("provisional")


@pytest.fixture
def live(member, make_user):
    """An approved :party: emoji."""
    pay(member)
    added = emoji.submit(member, "party", png())
    return emoji.approve(make_user("moderator"), added)


# --- buying and submitting --------------------------------------------------------------------


def test_catalogue_has_the_extra(db):
    assert Extra.objects.get(key="custom_emoji").stripe_price_setting == "STRIPE_PRICE_CUSTOM_EMOJI"


def test_submitting_needs_a_paid_credit(member):
    with pytest.raises(PermissionDenied):
        emoji.submit(member, "party", png())
    pay(member)
    added = emoji.submit(member, "party", png())
    assert added.status == "pending" and added.purchaser == member
    with pytest.raises(PermissionDenied):
        emoji.submit(member, "second", png())  # one emoji per purchase


@pytest.mark.parametrize("status", ["guest", "active"])
def test_guests_cannot_submit(make_user, status):
    guest = make_user("guest", status=status)
    pay(guest)
    with pytest.raises(PermissionDenied):
        emoji.submit(guest, "party", png())


def test_one_unused_purchase_at_a_time(member, settings):
    settings.STRIPE_PRICE_CUSTOM_EMOJI = "price_test"
    extra = Extra.objects.get(key="custom_emoji")
    assert can(member, "extras.buy", extra)
    pay(member)
    assert not can(member, "extras.buy", extra)
    emoji.submit(member, "party", png())
    assert can(member, "extras.buy", extra)


@pytest.mark.parametrize("name", ["a", "x" * 33, "Party", "par-ty", "par ty", ":party:"])
def test_name_rules(member, name):
    pay(member)
    with pytest.raises(ValidationError):
        emoji.submit(member, name, png())


def test_names_are_unique_whatever_their_status(member, make_user, live):
    other = make_user("provisional")
    pay(other)
    with pytest.raises(ValidationError):
        emoji.submit(other, "party", png())


@pytest.mark.parametrize("width,height,ok", [(64, 64, True), (384, 128, True), (385, 128, False),
                                             (64, 129, False)])
def test_size_and_shape_limits(member, width, height, ok):
    pay(member)
    if ok:
        assert emoji.submit(member, "shape", png(width, height))
    else:
        with pytest.raises(ValidationError):
            emoji.submit(member, "shape", png(width, height))


def test_file_size_limit(member, owner):
    set_site_setting(owner, "emoji.max_kb", 1)
    pay(member)
    big = io.BytesIO()
    Image.effect_noise((100, 100), 100).convert("RGB").save(big, "PNG")
    with pytest.raises(ValidationError):
        emoji.submit(member, "noisy", SimpleUploadedFile("n.png", big.getvalue()))


def test_animated_gifs_allowed_unless_turned_off(member, owner):
    pay(member)
    assert emoji.submit(member, "spin", gif()).image.mime == "image/gif"
    set_site_setting(owner, "emoji.allow_animated", False)
    pay(member)
    with pytest.raises(ValidationError):
        emoji.submit(member, "spin_two", gif())


# --- review, rejection credit, retirement, refund ---------------------------------------------


def test_any_staff_member_approves_but_not_their_own(member, make_user, general):
    pay(member)
    added = emoji.submit(member, "party", png())
    from tests.factories import grant

    scoped = make_user("tenured")
    grant(scoped, "moderator", scope_subforum=general)
    assert [i.obj for i in queue.items(scoped) if i.type == "emoji"] == [added]
    with pytest.raises(PermissionDenied):
        emoji.approve(make_user("full"), added)
    emoji.approve(scoped, added)
    with pytest.raises(PermissionDenied):
        emoji.approve(make_user("admin"), added)  # already handled
    mod = make_user("moderator")
    pay(mod)
    own = emoji.submit(mod, "mine", png())
    with pytest.raises(PermissionDenied):
        emoji.approve(mod, own)


def test_rejection_leaves_one_resubmission(member, make_user):
    pay(member)
    first = emoji.submit(member, "party", png())
    with pytest.raises(ValidationError):
        emoji.reject(make_user("moderator"), first, "")
    emoji.reject(make_user("moderator"), first, "Too close to an existing emoji")
    second = emoji.submit(member, "party_two", png())
    assert second.resubmission_of == first and second.charge == first.charge
    emoji.reject(make_user("moderator"), second, "Still no")
    with pytest.raises(PermissionDenied):
        emoji.submit(member, "party_three", png())


def test_admin_refunds_a_rejected_purchase(member, make_user, monkeypatch):
    refunded = []
    monkeypatch.setattr(stripe_api, "refund", refunded.append)
    pay(member)
    first = emoji.submit(member, "party", png())
    entitlement = member.entitlements.get(extra__key="custom_emoji")
    with pytest.raises(PermissionDenied):
        emoji.refund(make_user("admin"), entitlement)  # pending
    emoji.reject(make_user("moderator"), first, "no")
    with pytest.raises(PermissionDenied):
        emoji.refund(make_user("moderator"), entitlement)
    emoji.refund(make_user("admin"), entitlement)
    assert refunded == [first.charge.stripe_payment_intent_id]
    assert Charge.objects.get(pk=first.charge_id).status == "refunded"
    assert emoji.credit(member) is None


def test_staff_retire_with_no_refund(live, make_user):
    with pytest.raises(PermissionDenied):
        emoji.retire(make_user("full"), live)
    emoji.retire(make_user("moderator"), live)
    live.refresh_from_db()
    assert live.status == "retired"
    assert Charge.objects.get(pk=live.charge_id).status == "succeeded"


# --- rendering and display --------------------------------------------------------------------


@pytest.fixture
def post_with_emoji(make_user, general):
    author = make_user("full")
    thread = make_thread(general, author)
    services.reply(author, thread, "hello :party: and `:party:` at 10:30:45 :unknown:")
    return thread


def _body(client, thread):
    import re

    from core.watermark import strip

    page = strip(client.get(f"/t/{thread.pk}/").content.decode())
    return re.findall(r'<div class="body">(.*?)</div>', page, re.S)[-1]


def test_pending_rejected_and_retired_show_as_text(member, make_user, post_with_emoji):
    pay(member)
    pending = emoji.submit(member, "party", png())
    reader = signed_in(make_user("full"))
    assert "<img" not in _body(reader, post_with_emoji) and ":party:" in _body(reader, post_with_emoji)
    emoji.approve(make_user("moderator"), pending)
    assert '<img class="emoji"' in _body(reader, post_with_emoji)
    emoji.retire(make_user("moderator"), pending)
    assert "<img" not in _body(reader, post_with_emoji)


def test_images_carry_the_name_and_code_and_times_stay_text(live, make_user, post_with_emoji):
    body = _body(signed_in(make_user("full")), post_with_emoji)
    assert body.count('<img class="emoji"') == 1
    assert 'alt="party"' in body
    assert "<code>:party:</code>" in body and "10:30:45" in body and ":unknown:" in body


def test_each_display_setting(live, make_user, post_with_emoji):
    from accounts.models import User

    reader = make_user("full")
    client = signed_in(reader)
    assert f'src="/emoji/{live.pk}/image/"' in _body(client, post_with_emoji)
    User.objects.filter(pk=reader.pk).update(emoji_display="still")
    assert f'src="/emoji/{live.pk}/still/"' in _body(client, post_with_emoji)
    User.objects.filter(pk=reader.pk).update(emoji_display="names")
    body = _body(client, post_with_emoji)
    assert "<img" not in body and ":party:" in body
    client.post("/settings/display/", {"emoji_display": "images"})
    reader.refresh_from_db()
    assert reader.emoji_display == "images"


def test_animated_emoji_respect_reduced_motion(member, make_user, general):
    pay(member)
    spin = emoji.approve(make_user("moderator"), emoji.submit(member, "spin", gif()))
    author = make_user("full")
    thread = make_thread(general, author)
    services.reply(author, thread, "look :spin:")
    body = _body(signed_in(make_user("full")), thread)
    assert f'<source srcset="/emoji/{spin.pk}/still/" media="(prefers-reduced-motion: reduce)">' in body


def test_still_image_is_the_first_frame(member, make_user):
    pay(member)
    spin = emoji.approve(make_user("moderator"), emoji.submit(member, "spin", gif()))
    response = signed_in(make_user("full")).get(f"/emoji/{spin.pk}/still/")
    still = Image.open(io.BytesIO(response.content))
    assert response["Content-Type"] == "image/png" and getattr(still, "n_frames", 1) == 1


def test_pending_images_only_for_staff_and_the_purchaser(member, make_user):
    pay(member)
    pending = emoji.submit(member, "party", png())
    url = f"/emoji/{pending.pk}/image/"
    assert signed_in(make_user("full")).get(url).status_code == 404
    response = signed_in(make_user("moderator")).get(url)
    assert response.status_code == 200
    b"".join(response.streaming_content)


def test_submit_page(member, make_user):
    client = signed_in(member)
    assert client.get("/emoji/new/").status_code == 403
    pay(member)
    client.post("/emoji/new/", {"name": "wave", "image": png()})
    assert CustomEmoji.objects.get().name == "wave"
    assert b"wave" in signed_in(make_user("moderator")).get("/staff/queue/").content
