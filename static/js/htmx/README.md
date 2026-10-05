# HTMX

`htmx.min.js` is HTMX 2.0.11, unmodified, from the htmx.org package (github.com/bigskysoftware/htmx),
under the Zero-Clause BSD licence in `LICENSE.txt` (rule 73). It is configured in a `meta` tag in
`templates/base.html` with evaluated expressions, script tags, indicator styles and the history cache
off, so the content security policy holds (rules 64 and 77). `static/js/forum-htmx.js` adds the
waiting state, the inline error, the live-region announcements and focus handling.
