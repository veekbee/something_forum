"""Rule 22: uploaded images only, of the allowed types, re-encoded, within the per-post caps."""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from boards import images, services
from boards.models import Attachment


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def inline(general):
    general.settings["subforum.images"] = "inline"
    general.save()
    return general


def upload(fmt="JPEG", name=None, size=(40, 30), exif=False, mode="RGB"):
    buffer = io.BytesIO()
    image = Image.new(mode, size, (200, 30, 30) if mode == "RGB" else None)
    options = {}
    if exif:
        data = Image.Exif()
        data[0x010F] = "SpyCam"  # Make
        data[0x8825] = {2: (40.0, 26.0, 46.0)}  # GPS latitude
        options["exif"] = data.tobytes()
    image.save(buffer, fmt, **options)
    return SimpleUploadedFile(name or f"photo.{fmt.lower()}", buffer.getvalue())


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "GIF"])
def test_allowed_types_are_stored(make_user, inline, fmt):
    author = make_user("full")
    _, post = services.start_thread(author, inline, "Pictures", "![one](image:1)", [upload(fmt)])
    attachment = Attachment.objects.get(post=post)
    assert attachment.mime == f"image/{'jpeg' if fmt == 'JPEG' else fmt.lower()}"
    assert (attachment.width, attachment.height) == (40, 30)
    assert f'<img src="/attachments/{attachment.pk}/" alt="one"' in post.body_html


def test_metadata_is_gone_after_reencoding(make_user, inline):
    original = upload(exif=True)
    assert Image.open(io.BytesIO(original.read())).getexif()  # the test file really has EXIF
    original.seek(0)
    data, *_ = images.reencode(original)
    assert not Image.open(io.BytesIO(data)).getexif()


@pytest.mark.parametrize("content, name", [
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "drawing.svg"),
    (b"%PDF-1.4 not an image", "paper.pdf"),
    (b"just text", "notes.png"),
])
def test_other_types_are_refused(make_user, inline, content, name):
    with pytest.raises(ValidationError):
        services.start_thread(make_user("full"), inline, "Bad", "body", [SimpleUploadedFile(name, content)])
    assert not Attachment.objects.exists()


def test_formats_pillow_reads_but_the_forum_does_not_accept(make_user, inline):
    with pytest.raises(ValidationError):
        services.start_thread(make_user("full"), inline, "Bitmap", "body", [upload("BMP", name="x.bmp")])


def test_images_off_refuses_uploads(make_user, general):
    with pytest.raises(ValidationError):
        services.start_thread(make_user("full"), general, "Pics", "body", [upload()])


def test_count_cap(make_user, inline):
    with pytest.raises(ValidationError):
        services.start_thread(make_user("full"), inline, "Five", "body", [upload() for _ in range(5)])
    author = make_user("full")
    _, post = services.start_thread(author, inline, "Four", "body", [upload() for _ in range(4)])
    with pytest.raises(ValidationError):
        services.edit_post(author, post, "body", [upload()])


def test_size_cap(make_user, inline):
    inline.settings["subforum.max_image_mb"] = 1
    inline.save()
    big = upload()
    big.size = 2 * 1024 * 1024
    with pytest.raises(ValidationError):
        services.start_thread(make_user("full"), inline, "Big", "body", [big])


def test_unplaced_images_appear_at_the_end_and_attachments_mode_shows_beneath(make_user, inline, serious):
    author = make_user("full")
    _, post = services.start_thread(author, inline, "Pics", "no placement", [upload()])
    assert 'class="post-images"' in post.body_html
    serious.settings["subforum.images"] = "attachments"
    serious.save()
    _, post = services.start_thread(make_user("full"), serious, "Pics", "![one](image:1)", [upload()])
    assert 'class="post-attachments"' in post.body_html and "<img src" in post.body_html


def test_attachment_served_only_to_readers(client, make_user, inline):
    from tests.factories import enrol_totp

    author = make_user("full")
    _, post = services.start_thread(author, inline, "Pics", "x", [upload()])
    attachment = Attachment.objects.get(post=post)
    guest = make_user("guest")
    enrol_totp(guest)
    client.force_login(guest)
    assert client.get(f"/attachments/{attachment.pk}/").status_code == 404
    enrol_totp(author)
    client.force_login(author)
    response = client.get(f"/attachments/{attachment.pk}/")
    assert response.status_code == 200 and response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "private, no-store"
    assert b"".join(response.streaming_content)  # reading the body also closes the file
    client.logout()
    assert client.get(f"/attachments/{attachment.pk}/").status_code == 302


def test_placeholder_beyond_this_posts_uploads_shows_no_image(make_user, inline):
    author = make_user("full")
    _, post = services.start_thread(author, inline, "Pics", "![x](image:3)", [upload()])
    assert "image:3" not in post.body_html and 'class="post-images"' in post.body_html
