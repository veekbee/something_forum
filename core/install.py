"""Home-screen installability (docs/DESIGN.md, Platforms and Visual design; rule 14): the manifest,
the icons (the site's initials in the reading serif on the accent colour, or a supplied square PNG
named by settings.SITE_ICON), and a service worker that caches only the static shell and the offline
page. None of these carry member content, so they are served without a session."""

import io
import json

from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.templatetags.static import static
from django.views.decorators.http import require_GET

from core import registry

ACCENT = "#8a3b2f"  # the icon's ground: the accent colour
ICON_TEXT = "#fbfaf6"
THEME_COLOUR = "#2c2824"  # the masthead
BACKGROUND_COLOUR = "#f4f1ea"  # the page
SERIF = "fonts/source-serif-4/SourceSerif4Variable-Roman.otf.woff2"
# name: (size in pixels, maskable)
ICONS = {"icon-192": (192, False), "icon-512": (512, False), "maskable-512": (512, True), "apple-touch-icon": (180, False)}


def initials(name):
    return "".join(word[0] for word in name.split()[:2]).upper() or "F"


@require_GET
def manifest(request):
    name = registry.site_value("site.name")
    body = {
        "name": name,
        "short_name": name,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": THEME_COLOUR,
        "background_color": BACKGROUND_COLOUR,
        "icons": [
            {"src": f"/icons/{key}.png", "sizes": f"{size}x{size}", "type": "image/png",
             "purpose": "maskable" if maskable else "any"}
            for key, (size, maskable) in ICONS.items() if key != "apple-touch-icon"
        ],
    }
    return HttpResponse(json.dumps(body), content_type="application/manifest+json")


def _font(size):
    from django.contrib.staticfiles import finders
    from PIL import ImageFont

    path = finders.find(SERIF)
    if not path:
        return ImageFont.load_default(size=size)
    font = ImageFont.truetype(path, size)
    try:
        font.set_variation_by_axes([600, 20])  # semibold, at the sturdier text optical size
    except (OSError, AttributeError):
        pass
    return font


def _draw(text, size, maskable):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (size, size), ACCENT)
    draw = ImageDraw.Draw(image)
    # A maskable icon keeps its content inside the central safe zone, which is 80% of the width.
    font = _font(int(size * (0.34 if maskable else 0.44)))
    draw.text((size / 2, size / 2), text, fill=ICON_TEXT, font=font, anchor="mm")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _supplied(size, maskable):
    """The configured square PNG, resized; a maskable icon gets it inside the safe zone."""
    from django.conf import settings
    from django.contrib.staticfiles import finders
    from PIL import Image

    path = finders.find(settings.SITE_ICON) if settings.SITE_ICON else None
    if not path:
        return None
    with Image.open(path) as source:
        logo = source.convert("RGBA")
    canvas = Image.new("RGBA", (size, size), ACCENT)
    inner = int(size * 0.8) if maskable else size
    logo = logo.resize((inner, inner), Image.LANCZOS)
    canvas.paste(logo, ((size - inner) // 2, (size - inner) // 2), logo)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue()


@require_GET
def icon(request, name):
    if name not in ICONS:
        raise Http404
    text = initials(registry.site_value("site.name"))
    size, maskable = ICONS[name]
    from django.conf import settings

    key = f"install:icon:v2:{name}:{text}:{settings.SITE_ICON}"
    data = cache.get(key)
    if data is None:
        data = _supplied(size, maskable) or _draw(text, size, maskable)
        cache.set(key, data, timeout=None)
    response = HttpResponse(data, content_type="image/png")
    response["Cache-Control"] = "public, max-age=86400"
    return response


SERVICE_WORKER = """// Caches only the static shell and the offline page; never forum pages, posts, DMs or
// attachments (docs/DESIGN.md, Platforms). Logout clears this cache with Clear-Site-Data.
const CACHE = "shell-v2";
const SHELL = %(shell)s;
const OFFLINE = "/offline/";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    // Pages always come from the network; only when there is none is the offline page shown.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE)));
    return;
  }
  if (SHELL.includes(url.pathname)) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
  }
});
"""


def shell_paths():
    paths = [static("css/forum.css"), static("js/mentions.js"), static("js/sw-register.js"), "/offline/",
             static(SERIF), static("fonts/source-serif-4/SourceSerif4Variable-Italic.otf.woff2"),
             static("fonts/source-sans-3/SourceSans3VF-Upright.otf.woff2"),
             static("fonts/source-sans-3/SourceSans3VF-Italic.otf.woff2")]
    paths += [f"/icons/{key}.png" for key in ICONS]
    return paths


@require_GET
def service_worker(request):
    response = HttpResponse(SERVICE_WORKER % {"shell": json.dumps(shell_paths())},
                            content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    return response
