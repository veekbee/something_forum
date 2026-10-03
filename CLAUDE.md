# CLAUDE.md

Guidance for Claude Code sessions in this repository.

## What this is

A paid, invite-only discussion forum. Every member is sponsored by another member, moderation is done by people, and nothing is visible without an authenticated, TOTP-verified session. The structure follows vBulletin: sub-forums contain threads, threads contain posts, and each sub-forum has its own rules.

## Source of truth

`docs/DESIGN.md` is the specification. Read it before starting any task, especially the **Implementation brief** section: the stack, data model, settings registry, the rules the code must enforce, and milestone 1.

- Do not change a design decision on your own. If a task conflicts with the design doc, or the doc is silent on something that matters, stop and ask. Do not guess.
- Values marked "Proposed" or "Assumed" in the settings registry are defaults. Implement them as registry entries, never as constants.
- Items under "Still open" in the doc are not decided. Do not build features that depend on them.

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

Milestone 1, as defined in `docs/DESIGN.md` under "First Claude Code session: milestone 1": scaffold, data model, permission service, settings registry, seed data, authentication with TOTP, and tests. No member-facing UI beyond login and TOTP enrolment.
