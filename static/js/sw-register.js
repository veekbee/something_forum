// Registers the shell-only service worker that makes the site installable (docs/DESIGN.md, Platforms).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" });
}
