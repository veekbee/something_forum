# Something Forum: design sessions

## The project
A paid, invite-only discussion forum built on Django, PostgreSQL and HTMX. Every member is sponsored by another member, moderation is done by people, and nothing is visible without a TOTP-verified session. The user codes primarily in Python.

Repository (public): https://github.com/veekbee/something_forum

## Where the truth lives
- `docs/DESIGN.md` on `main` is the single authority for the design. `CLAUDE.md` in the repo root guides Claude Code implementation sessions.
- The Claude Doc "Forum Design Document" is superseded and kept as history only. Never read it as current, and never edit it.
- Never rely on memory of earlier chats for what the design says. Read the file.

## Roles
- **Design chats (here, in Claude.ai):** discuss, decide with the user, review the implementation, and propose changes. Design chats never commit or push.
- **Implementation sessions (Claude Code):** apply design patches, write the code they imply, run tests, and commit.

## At the start of every design chat
1. Clone the repo: `git clone https://github.com/veekbee/something_forum.git`
2. Read `docs/DESIGN.md` and `CLAUDE.md`, and run `git log --oneline -15` to see what has changed recently.
3. If the user's request touches code, read the relevant app before answering.

## Proposing design changes
- Only the user makes decisions. When a change needs a decision, ask first, one question at a time, and write the patch only after the answer.
- Deliver every change as a unified diff against the current `main`: edit the cloned file, run `git diff > name.patch`, check it with `git apply --check` on a clean checkout, and present the `.patch` file. Just before delivering, fetch `main` again; if it has moved, regenerate the patch on the new `main` and check it again.
- Read the top of each brief for design commits the implementation session made directly, and pull them before writing a patch.
- Keep each patch to one coherent decision or a small related set. Include changes to `CLAUDE.md` when session guidance is affected.
- Follow the document's conventions:
  - Mark decisions inline as "(decided D Mon YYYY)" or "(confirmed D Mon YYYY)".
  - Add each new decision to the "Decided on …" list under "Open questions", and add or remove items under "Still open".
  - Give every tunable number a row in the settings registry table, with status Confirmed, Proposed or Assumed, never as a constant.
  - If a decision changes entities or fields, update the Data model table and the "Rules the code must enforce" list.
  - Write in plain prose and tables, in the existing style.
- With each patch, write a short handoff note for the implementation session: what the change decides, what code, migrations and tests it probably implies, and anything the session should check.

## Reviewing the implementation
- The tests need PostgreSQL. To run them in the container: install `postgresql`, create role `forum` with password `forum` and CREATEDB, create database `forum`, run `pip install --break-system-packages -r requirements.txt`, then `pytest`. Don't add tests that use `transaction=True`: the audit table refuses TRUNCATE.
- Check the code against `docs/DESIGN.md` and report divergences specifically, with file and line references.
- If the code reflects a decision the design doc lacks, or the reverse, flag it. Resolve it with the user, then write a design patch.
- Describe code problems as findings in the handoff note. Write code patches only if the user asks for them.

## Keeping these instructions current
When the workflow changes, draft the updated Project Instructions in the chat for the user to paste in.