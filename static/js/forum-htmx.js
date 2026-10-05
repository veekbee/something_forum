// Partial-page updates (docs/DESIGN.md, Partial-page updates). HTMX does the requests; this adds the
// waiting state, the inline error, the live-region announcements and focus. No spinner, no motion.
(function () {
  "use strict";
  var region = function () { return document.getElementById("live"); };

  document.body.addEventListener("htmx:beforeRequest", function (event) {
    var form = event.detail.elt.closest("form") || event.detail.elt;
    form.querySelectorAll("button").forEach(function (button) {
      button.dataset.label = button.textContent;
      button.textContent = button.dataset.wait || "Saving…";
      button.disabled = true;
    });
    var old = form.parentNode && form.parentNode.querySelector(":scope > .htmx-error");
    if (old) { old.remove(); }
  });

  document.body.addEventListener("htmx:afterRequest", function (event) {
    var form = event.detail.elt.closest("form") || event.detail.elt;
    if (document.body.contains(form)) {
      form.querySelectorAll("button").forEach(function (button) {
        if (button.dataset.label) { button.textContent = button.dataset.label; }
        button.disabled = false;
      });
      var xhr = event.detail.xhr;
      if (event.detail.successful && form.hasAttribute("data-reset") && xhr && !xhr.getResponseHeader("HX-Retarget")) {
        form.reset();
      }
    }
  });

  function failed(event) {
    var form = event.detail.elt.closest("form") || event.detail.elt;
    if (!form.parentNode || (event.detail.xhr && event.detail.xhr.getResponseHeader("HX-Redirect"))) { return; }
    var note = document.createElement("p");
    note.className = "errors htmx-error";
    note.setAttribute("role", "alert");
    note.textContent = "That didn't work. Reload the page.";
    form.insertAdjacentElement("afterend", note);
  }
  document.body.addEventListener("htmx:responseError", failed);
  document.body.addEventListener("htmx:sendError", failed);

  document.body.addEventListener("announce", function (event) {
    var live = region();
    if (live) { live.textContent = ""; live.textContent = event.detail.value; }
  });

  // Focus moves to the new element, or to the element in it marked data-focus. htmx:load fires on each
  // element a swap adds; the page's own first load is skipped so focus is never taken on arrival.
  document.body.addEventListener("htmx:load", function (event) {
    var elt = event.target;
    if (!elt || elt === document.body || !elt.isConnected) { return; }
    var target = elt.matches("[data-focus]") ? elt : elt.querySelector("[data-focus]");
    if (target) {
      if (!target.hasAttribute("tabindex") && !/^(A|BUTTON|INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) {
        target.setAttribute("tabindex", "-1");
      }
      target.focus();
    }
  });
})();
