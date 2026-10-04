"""Per-session watermarking (docs/DESIGN.md, Against members scraping or sharing credentials; rule 61).

Post bodies and DM messages carry an invisible mark identifying the session they were served to and
the day. Marks are added to each response on top of the stored HTML and are never stored or indexed.
The characters and positions are configuration (settings.WATERMARK_CHARS and WATERMARK_EVERY_WORDS),
not code: only zero-width characters with no other job, never U+200C or U+200D, which build emoji
sequences and are needed in Persian and several Indian scripts. Everything members submit has the
configured characters, and only those, stripped. This is a tracing aid against casual leaks, not
strong protection: the public repository shows the scheme.

A mark is one contiguous run of 56 characters, one per bit: a 32-bit session seed, a 16-bit day
number and an 8-bit check."""

import re
import zlib
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from core import registry

FORBIDDEN = {"‌", "‍"}
EPOCH = date(2026, 1, 1)
BITS = 56


def characters():
    chars = settings.WATERMARK_CHARS
    if len(chars) != 2 or chars[0] == chars[1] or FORBIDDEN & set(chars):
        raise ImproperlyConfigured("WATERMARK_CHARS must be two different zero-width characters, never U+200C or U+200D")
    return chars


def strip(text):
    """Remove the watermark characters, and only those, from submitted text."""
    if not isinstance(text, str):
        return text
    zero, one = characters()
    return text.replace(zero, "").replace(one, "")


# --- encoding ------------------------------------------------------------------------------


def _check(seed, day):
    return zlib.crc32(seed.to_bytes(4, "big") + day.to_bytes(2, "big")) & 0xFF


def encode(seed, day):
    value = (seed << 24) | (day << 8) | _check(seed, day)
    zero, one = characters()
    return "".join(one if value >> (BITS - 1 - i) & 1 else zero for i in range(BITS))


def decode(run):
    """(seed, day) from a run of exactly BITS characters, or None if the check fails."""
    zero, one = characters()
    if len(run) != BITS or set(run) - {zero, one}:
        return None
    value = int("".join("1" if c == one else "0" for c in run), 2)
    seed, day, check = value >> 24, (value >> 8) & 0xFFFF, value & 0xFF
    return (seed, day) if _check(seed, day) == check else None


def day_number(today):
    return (today - EPOCH).days & 0xFFFF


def day_date(number):
    return EPOCH + timedelta(days=number)


def seed_value(watermark_seed):
    try:
        return int(watermark_seed, 16) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None


# --- marking a response --------------------------------------------------------------------

_TAG = re.compile(r"(<[^>]*>)")
_WORD = re.compile(r"\S+")


def mark_html(html, run):
    """Insert `run` after the first word of the text and then after every Nth word, never inside a
    tag, attribute, code or preformatted block."""
    every = max(int(settings.WATERMARK_EVERY_WORDS), 1)
    out, words, skip = [], 0, 0
    for part in _TAG.split(html):
        if part.startswith("<"):
            name = re.match(r"</?\s*([a-zA-Z0-9]+)", part)
            if name and name.group(1).lower() in ("code", "pre"):
                skip += -1 if part.startswith("</") else 1
                skip = max(skip, 0)
            out.append(part)
            continue
        if skip or not part.strip():
            out.append(part)
            continue
        pieces, last = [], 0
        for match in _WORD.finditer(part):
            words += 1
            if words == 1 or words % every == 0:
                pieces.append(part[last:match.end()])
                pieces.append(run)
                last = match.end()
        pieces.append(part[last:])
        out.append("".join(pieces))
    return "".join(out)


def for_request(request, html):
    """The stored HTML, marked for this request's session when watermarking is on."""
    if not registry.site_value("watermark.enabled"):
        return html
    record = getattr(request, "user_session", None)
    seed = seed_value(record.watermark_seed) if record is not None else None
    if seed is None:
        return html
    from django.utils import timezone

    return mark_html(html, encode(seed, day_number(timezone.localdate())))


# --- tracing -------------------------------------------------------------------------------


def find(text):
    """Every (seed, day) mark in a pasted excerpt, in order, without repeats."""
    zero, one = characters()
    found = []
    for match in re.finditer(f"[{re.escape(zero)}{re.escape(one)}]{{{BITS},}}", text or ""):
        run = match.group(0)
        for start in range(0, len(run) - BITS + 1, BITS):
            decoded = decode(run[start:start + BITS])
            if decoded is not None and decoded not in found:
                found.append(decoded)
    return found
