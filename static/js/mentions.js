// Mention autocomplete: typing "@" and a few letters in a post offers matching members.
(function () {
  "use strict";
  document.querySelectorAll("textarea[data-mentions]").forEach(function (box) {
    var list = document.createElement("ul");
    list.className = "mention-list";
    list.hidden = true;
    box.insertAdjacentElement("afterend", list);
    var timer = null;

    function current() {
      var before = box.value.slice(0, box.selectionStart);
      var match = /(^|[^\w@])@([a-z0-9-]{1,40})$/.exec(before);
      return match ? match[2] : null;
    }

    function choose(slug) {
      var start = box.selectionStart;
      var before = box.value.slice(0, start).replace(/@[a-z0-9-]*$/, "@" + slug + " ");
      box.value = before + box.value.slice(start);
      box.selectionStart = box.selectionEnd = before.length;
      list.hidden = true;
      box.focus();
    }

    box.addEventListener("input", function () {
      clearTimeout(timer);
      var prefix = current();
      if (!prefix) { list.hidden = true; return; }
      timer = setTimeout(function () {
        fetch("/members/autocomplete/?q=" + encodeURIComponent(prefix), { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : { results: [] }; })
          .then(function (data) {
            list.replaceChildren();
            data.results.forEach(function (m) {
              var item = document.createElement("li");
              var button = document.createElement("button");
              button.type = "button";
              button.textContent = "@" + m.slug + " · " + m.name;
              button.addEventListener("click", function () { choose(m.slug); });
              item.appendChild(button);
              list.appendChild(item);
            });
            list.hidden = data.results.length === 0;
          });
      }, 150);
    });
  });
})();
