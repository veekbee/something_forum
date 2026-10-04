"""Image uploads (design rule 22). Only JPEG, PNG, WebP and GIF, identified by decoding the file
rather than trusting its name or declared type. Every image is re-encoded before storage, which
drops camera metadata such as location and neutralises malformed files. SVG and everything else is
refused."""

import io
import uuid
import warnings

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp"),
           "GIF": ("gif", "image/gif")}
# Refuse images that would expand to more pixels than this when decoded. Pillow checks the
# declared size before decoding and warns; _open turns that warning into a refusal.
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def check_caps(subforum, files, existing=0):
    """Refuse the whole upload if it breaks the sub-forum's per-post caps."""
    mode = subforum.setting("subforum.images")
    if files and mode == "off":
        raise ValidationError("Images are not allowed in this sub-forum.")
    limit = subforum.setting("subforum.max_images_per_post")
    if existing + len(files) > limit:
        raise ValidationError(f"A post can have at most {limit} images.")
    max_mb = subforum.setting("subforum.max_image_mb")
    for upload in files:
        if upload.size > max_mb * 1024 * 1024:
            raise ValidationError(f"{upload.name} is larger than {max_mb} MB.")


def _open(upload):
    data = upload.read()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning,
                Image.DecompressionBombError) as exc:
            raise ValidationError(f"{upload.name} is not an image the forum accepts.") from exc
    if image.format not in ALLOWED:
        raise ValidationError(f"{upload.name}: only JPEG, PNG, WebP and GIF images are accepted.")
    if image.width * image.height > MAX_PIXELS:
        raise ValidationError(f"{upload.name} has too many pixels.")
    return image


def reencode(upload):
    """Return (bytes, extension, mime, width, height) for a clean copy of the image."""
    image = _open(upload)
    fmt = image.format
    ext, mime = ALLOWED[fmt]
    out = io.BytesIO()
    if fmt == "GIF":
        frames = []
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            frames.append(image.convert("RGBA"))
        frames[0].save(out, "GIF", save_all=len(frames) > 1, append_images=frames[1:],
                       loop=image.info.get("loop", 0), duration=image.info.get("duration", 100), disposal=2)
        width, height = frames[0].size
    else:
        clean = ImageOps.exif_transpose(image)
        if fmt == "JPEG":
            clean = clean.convert("RGB")
        elif clean.mode not in ("RGB", "RGBA", "L", "LA"):
            clean = clean.convert("RGBA")
        # A fresh image holds pixels only: no EXIF, XMP, ICC text chunks or comments.
        pixels = Image.new(clean.mode, clean.size)
        pixels.paste(clean)
        options = {"quality": 85} if fmt in ("JPEG", "WEBP") else {"optimize": True}
        pixels.save(out, fmt, **options)
        width, height = pixels.size
    return out.getvalue(), ext, mime, width, height


def store(post, uploader, upload):
    from boards.models import Attachment

    data, ext, mime, width, height = reencode(upload)
    key = default_storage.save(f"attachments/{uuid.uuid4().hex}.{ext}", ContentFile(data))
    return Attachment.objects.create(
        post=post, uploader=uploader, storage_key=key, filename=upload.name[:255], mime=mime,
        size_bytes=len(data), width=width, height=height,
    )
