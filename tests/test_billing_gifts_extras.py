"""Build step 5 part 4: gifts and paid extras (rules 41 and 49)."""

import io

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts import roles
from accounts.models import User
from billing import extras, stripe_api, webhooks
from billing.models import Charge, Entitlement, Extra, Gift, Subscription
from core.permissions import can
from tests.factories import enrol_totp, sponsor

_ids = iter(range(1, 10**6))


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.STRIPE_PRICE_AVATAR_CAPTION = "price_avatar"
    settings.STRIPE_PRICE_GIFT = "price_gift"


@pytest.fixture
def fake_checkout(monkeypatch):
    calls = []
    monkeypatch.setattr(stripe_api, "create_checkout_session",
                        lambda **p: calls.append(p) or {"url": "https://checkout.stripe.test/x"})
    return calls


def _completed(user, purpose, amount, **meta):
    return webhooks.process({"id": f"evt_{next(_ids)}", "type": "checkout.session.completed", "data": {"object": {
        "id": f"cs_{next(_ids)}", "mode": "payment", "payment_status": "paid", "amount_total": amount,
        "currency": "usd", "payment_intent": f"pi_{next(_ids)}",
        "metadata": {"purpose": purpose, "user": str(user.pk), **meta},
    }}})


def _image():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 120, 200)).save(buffer, "PNG")
    return SimpleUploadedFile("me.png", buffer.getvalue())


# --- gifts ---------------------------------------------------------------------------------


def test_only_a_sponsor_gifts_and_only_to_their_own_guest(make_user):
    guest, own_sponsor = make_user("guest"), make_user("full")
    sponsor(own_sponsor, guest)
    assert can(own_sponsor, "billing.gift", guest)
    assert not can(make_user("full"), "billing.gift", guest)
    provisional = make_user("provisional")
    sponsor(own_sponsor, provisional)
    assert not can(own_sponsor, "billing.gift", provisional)


def test_gift_is_a_one_off_payment(make_user, fake_checkout):
    guest, own_sponsor = make_user("guest"), make_user("full")
    sponsor(own_sponsor, guest)
    extras.gift_checkout(own_sponsor, guest, "https://x/ok", "https://x/back")
    [params] = fake_checkout
    assert params["mode"] == "payment" and params["line_items"] == [{"price": "price_gift", "quantity": 1}]
    assert params["metadata"]["invitee"] == str(guest.pk)


def test_accepting_a_gift_moves_the_guest_to_provisional_for_a_year(make_user):
    guest, own_sponsor = make_user("guest"), make_user("full")
    sponsor(own_sponsor, guest)
    _completed(own_sponsor, "gift", 2500, invitee=str(guest.pk))
    gift = Gift.objects.get()
    assert gift.status == "paid" and gift.charge.user == own_sponsor
    guest.refresh_from_db()
    assert guest.status == User.Status.GUEST  # nothing happens until the Guest accepts
    with pytest.raises(PermissionDenied):
        extras.accept_gift(own_sponsor, gift)
    extras.accept_gift(guest, gift)
    guest.refresh_from_db()
    assert guest.status == User.Status.ACTIVE and roles.trust_role(guest).name == "provisional"
    gift.refresh_from_db()
    sub = Subscription.objects.get(user=guest)
    assert sub.stripe_subscription_id == "" and gift.status == "accepted"
    assert 364 <= (sub.current_period_end - gift.accepted_at).days <= 366
    assert not can(own_sponsor, "billing.gift", guest)


# --- extras --------------------------------------------------------------------------------


def test_the_catalogue_is_seeded(seeded):
    extra = Extra.objects.get(key="avatar_caption")
    assert (extra.stripe_price_setting, extra.min_role) == ("STRIPE_PRICE_AVATAR_CAPTION", "provisional")


def test_extras_are_for_provisional_and_above_and_bought_once(make_user, fake_checkout):
    extra = Extra.objects.get(key="avatar_caption")
    assert not can(make_user("guest"), "extras.buy", extra)
    member = make_user("provisional")
    extras.extra_checkout(member, extra, "https://x/ok", "https://x/back")
    assert fake_checkout[0]["line_items"] == [{"price": "price_avatar", "quantity": 1}]
    _completed(member, "extra", 1000, extra="avatar_caption")
    assert extras.has_extra(member, "avatar_caption")
    assert not can(member, "extras.buy", extra)


def test_avatar_and_caption(make_user):
    member = make_user("provisional")
    with pytest.raises(PermissionDenied):
        extras.set_avatar_and_caption(member, caption="hello")
    _completed(member, "extra", 1000, extra="avatar_caption")
    with pytest.raises(ValidationError):
        extras.set_avatar_and_caption(member, caption="x" * 41)
    extras.set_avatar_and_caption(member, _image(), "  Gardener   and   writer ")
    member.refresh_from_db()
    assert member.caption == "Gardener and writer" and member.avatar.post is None
    with pytest.raises(ValidationError):
        extras.set_avatar_and_caption(member, SimpleUploadedFile("x.svg", b"<svg/>"))


def test_avatars_show_to_members_and_default_to_initials(client, make_user):
    from django.template import Context, Template

    member, viewer = make_user("provisional"), make_user("full")
    html = Template("{% load forum %}{% avatar m %}").render(Context({"m": member}))
    assert "<span class=\"avatar avatar-c" in html and "style=" not in html
    _completed(member, "extra", 1000, extra="avatar_caption")
    extras.set_avatar_and_caption(member, _image(), "")
    member.refresh_from_db()
    enrol_totp(viewer)
    client.force_login(viewer)
    response = client.get(f"/attachments/{member.avatar_id}/")
    assert response.status_code == 200 and response["Content-Type"] == "image/png"
    assert b"".join(response.streaming_content)


def test_extras_survive_a_lapse(make_user):
    member = make_user("provisional")
    _completed(member, "extra", 1000, extra="avatar_caption")
    User.objects.filter(pk=member.pk).update(status=User.Status.READ_ONLY)
    member.refresh_from_db()
    assert can(member, "profile.customise")


def _with_extra(make_user):
    member = make_user("provisional")
    _completed(member, "extra", 1000, extra="avatar_caption")
    extras.set_avatar_and_caption(member, _image(), "Offensive")
    return member, Entitlement.objects.get(user=member)


def test_any_staff_member_resets_with_a_preset_reason(make_user):
    from moderation.models import ModerationAction

    member, _ = _with_extra(make_user)
    mod = make_user("moderator")
    with pytest.raises(ValidationError):
        extras.reset_to_default(mod, member, "")
    with pytest.raises(PermissionDenied):
        extras.reset_to_default(make_user("full"), member, "spam")
    action = extras.reset_to_default(mod, member, "personal_attack")
    member.refresh_from_db()
    assert member.avatar is None and member.caption == "" and extras.has_extra(member, "avatar_caption")
    assert (action.kind, action.status, action.is_public, action.approved_by) == (
        ModerationAction.Kind.AVATAR_RESET, "active", False, None)
    assert action.internal_reason == "Personal attack"


def test_only_admins_and_owners_revoke_an_extra_publicly(client, make_user):
    from moderation.models import ModerationAction

    member, entitlement = _with_extra(make_user)
    with pytest.raises(PermissionDenied):
        extras.revoke(make_user("moderator"), entitlement, "misuse", "Lost the custom avatar")
    admin = make_user("admin")
    with pytest.raises(ValidationError):
        extras.revoke(admin, entitlement, "misuse", "")
    action = extras.revoke(admin, entitlement, "repeated misuse", "Lost the custom avatar for misuse")
    assert action.kind == ModerationAction.Kind.EXTRA_REVOCATION and action.is_public
    assert not extras.has_extra(member, "avatar_caption")
    entitlement.refresh_from_db()
    assert entitlement.revoked_by_action == action
    member.refresh_from_db()
    assert member.avatar is None and member.caption == ""
    assert not Charge.objects.filter(user=member, status="refunded").exists()
    reader = make_user("provisional")
    enrol_totp(reader)
    client.force_login(reader)
    assert b"Lost the custom avatar for misuse" in client.get(f"/members/{member.slug}/rap-sheet/").content


def test_admins_and_owners_restore_and_both_stay_on_the_record(client, make_user):
    from moderation.models import ModerationAction

    member, entitlement = _with_extra(make_user)
    admin = make_user("admin")
    with pytest.raises(PermissionDenied):
        extras.restore(admin, entitlement, "x", "y")  # not revoked
    revocation = extras.revoke(admin, entitlement, "misuse", "Lost the custom avatar")
    with pytest.raises(PermissionDenied):
        extras.restore(make_user("moderator"), entitlement, "mistake", "Avatar restored")
    restoration = extras.restore(make_user("owner"), entitlement, "mistake", "Custom avatar restored")
    assert extras.has_extra(member, "avatar_caption")
    revocation.refresh_from_db()
    assert revocation.status == ModerationAction.Status.REVERSED
    assert restoration.kind == ModerationAction.Kind.EXTRA_RESTORATION and restoration.related_action == revocation
    reader = make_user("provisional")
    enrol_totp(reader)
    client.force_login(reader)
    page = client.get(f"/members/{member.slug}/rap-sheet/").content
    assert b"Lost the custom avatar" in page and b"Custom avatar restored" in page


def test_revoke_from_the_staff_view(client, make_user):
    member, entitlement = _with_extra(make_user)
    admin = make_user("admin")
    enrol_totp(admin)
    client.force_login(admin)
    assert b"Revoke (public, not refunded)" in client.get(f"/staff/members/{member.slug}/").content
    client.post(f"/staff/entitlements/{entitlement.pk}/revoke/",
                {"internal_reason": "misuse", "public_summary": "Lost the custom avatar"})
    assert not extras.has_extra(member, "avatar_caption")
    assert b"Restore (public)" in client.get(f"/staff/members/{member.slug}/").content


def test_a_moderator_asks_for_revocation_by_reporting_and_escalating(make_user):
    """Rule 49: a Moderator who thinks an extra should go files a report about the member and
    escalates it, so it waits for an Admin or Owner."""
    from moderation import queue, reports

    member, entitlement = _with_extra(make_user)
    mod = make_user("moderator")
    report, _ = reports.report(mod, member, "other", "Avatar misuse again; please consider revoking the extra")
    reports.escalate(mod, report, "Third reset this month")
    report.refresh_from_db()
    admin = make_user("admin")
    assert any(i.obj == report for i in queue.items(admin))
    assert not can(mod, "report.resolve", report)
    assert can(admin, "report.resolve", report)
