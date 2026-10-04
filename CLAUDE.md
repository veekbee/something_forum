# CLAUDE.md

Guidance for Claude Code sessions in this repository.

## What this is

A paid, invite-only discussion forum. Every member is sponsored by another member, moderation is done by people, and nothing is visible without an authenticated, TOTP-verified session. The structure follows vBulletin: sub-forums contain threads, threads contain posts, and each sub-forum has its own rules.

## Source of truth

`docs/DESIGN.md` is the specification. Read it before starting any task, especially the **Implementation brief** section: the stack, data model, settings registry, the rules the code must enforce, and milestone 1.

- Do not change a design decision on your own. If a task conflicts with the design doc, or the doc is silent on something that matters, stop and ask. Do not guess.
- Values marked "Proposed" or "Assumed" in the settings registry are defaults. Implement them as registry entries, never as constants.
- Items under "Still open" in the doc are not decided. Do not build features that depend on them.

## Design changes

Design chats in Claude.ai propose changes as unified diffs against `docs/DESIGN.md` (and sometimes this file), following the instructions in `docs/DESIGN_SESSIONS.md`. Each patch comes with a handoff note on the code, migrations and tests it implies. When the user hands you one:

1. Check it applies cleanly with `git apply --check`. If it doesn't, or if it contradicts code already built or another part of the design, report the conflict instead of merging by hand.
2. Apply it, then make any code, migration, registry or test changes it implies in the same piece of work.
3. Commit the design change and the code that implements it together, with a message that says the design was revised and why.

Never edit the design decisions in `docs/DESIGN.md` on your own initiative. Propose the change to the user first. Updating status lines (for example, marking a milestone done) is fine.

## Stack

Python 3.12+, Django 5.x, PostgreSQL 16+, HTMX with server-rendered templates, django-allauth (email/password, mandatory TOTP), Stripe, django-storages (S3-compatible, signed URLs only), pytest-django, Docker Compose.

## Non-negotiables

- Deny by default. All authorisation goes through the single permission service `can(actor, action, target)`. Views never check roles directly.
- No unauthenticated access except login, invitation acceptance, Stripe webhooks and legal pages.
- Role and sponsorship changes are new rows, never updates.
- Posts are soft-deleted only. The audit log is append-only at the database level, and audit entries are written in the same transaction as the action they record.
- Admins and Owners may act alone on any moderation action and are exempt from sponsorship rules. Everyone else follows the two-person rule.

## Public repository hygiene

This repository is public.

- Never commit secrets. Configuration comes from environment variables, documented in `.env.example`.
- Watermarking parameters live in configuration, not code.
- Never put real member data in fixtures, tests or seed data.

## Working conventions

- Every behaviour in "Rules the code must enforce" has tests. Write the test along with the feature.
- Keep migrations small and reviewable, one concern each.
- `manage.py seed` must stay idempotent.
- At the end of each session, summarise what was built, what was deferred, and any question for the design doc.

## Current milestone

Done: milestone 1 (scaffold, data model, permission service, registry, TOTP sign-in), milestone 2 (onboarding and the promotion workflow), build step 3 (forum pages), build step 4 (moderation and direct messages) and build step 5 (billing), as defined in `docs/DESIGN.md`. One part of step 5 waits on design: moving a lapsed sponsor's sponsees into sponsorship transfer, which the design has not specified (see the sponsorship-transfer brief); step 5 also uses interim readings for when billing launches, Permanent Ban sponsor reviews, redaction outside the Graveyard and revoking extras. Check them when the next design patch lands. Next in the build order is step 6: the anti-scraping layer, the mobile-first UI and home-screen installability.
