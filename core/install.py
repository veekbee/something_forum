"""Home-screen installability (docs/DESIGN.md, Platforms; rule 14): the manifest, placeholder icons
(the site's initials on a solid colour until the visual pass), and a service worker that caches only
the static shell and the offline page. None of these carry member content, so they are served
without a session."""

import io
import json

from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.templatetags.static import static
from django.views.decorators.http import require_GET

from core import registry

THEME_COLOUR = "#2f5d8a"
BACKGROUND_COLOUR = "#faf9f6"
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


def _draw(text, size, maskable):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (size, size), THEME_COLOUR)
    draw = ImageDraw.Draw(image)
    # A maskable icon keeps its content inside the central safe zone, which is 80% of the width.
    font = ImageFont.load_default(size=int(size * (0.32 if maskable else 0.42)))
    draw.text((size / 2, size / 2), text, fill="#ffffff", font=font, anchor="mm")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@require_GET
def icon(request, name):
    if name not in ICONS:
        raise Http404
    text = initials(registry.site_value("site.name"))
    size, maskable = ICONS[name]
    key = f"install:icon:{name}:{text}"
    data = cache.get(key)
    if data is None:
        data = _draw(text, size, maskable)
        cache.set(key, data, timeout=None)
    response = HttpResponse(data, content_type="image/png")
    response["Cache-Control"] = "public, max-age=86400"
    return response


SERVICE_WORKER = """// Caches only the static shell and the offline page; never forum pages, posts, DMs or
// attachments (docs/DESIGN.md, Platforms). Logout clears this cache with Clear-Site-Data.
const CACHE = "shell-v1";
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
    paths = [static("css/site.css"), static("js/mentions.js"), static("js/sw-register.js"), "/offline/"]
    paths += [f"/icons/{key}.png" for key in ICONS]
    return paths


@require_GET
def service_worker(request):
    response = HttpResponse(SERVICE_WORKER % {"shell": json.dumps(shell_paths())},
                            content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    return response
