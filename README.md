# Something Forum

A paid, invite-only discussion forum. Every member is sponsored by another member, moderation is
done by people, and nothing is visible without a signed-in session that has passed two-factor
authentication.

The specification is [docs/DESIGN.md](docs/DESIGN.md). Read it before changing anything; the
**Implementation brief** section defines the data model, the settings registry and the rules the
code must enforce. Guidance for Claude Code sessions is in [CLAUDE.md](CLAUDE.md).

## Status

Built so far: milestone 1 (data model, permission service, settings registry, TOTP sign-in),
milestone 2 (invitations with sponsorship slots and a waitlist, Admin review at `/staff/onboarding/`,
complimentary membership, promotions) and build step 3 (the forum: sub-forums, threads, the Markdown
editor with mentions, quotes and image uploads, editing and deleting, the Thread Graveyard and Thread
Classics, profiles and search) and build step 4 (direct messages with blocking, the moderation queue
with reports and automatic flags, scoped actions and Probation, the per-member view, the audit log,
the Mod feedback feed, and notifications with pointer-only email) and build step 5 (Stripe billing:
annual membership, the lapse clock, comps, ban payments, the Permanent Ban, gifts, paid extras, the
Rap Sheet). Pages are plain; the visual design comes later. Admins and Owners can inspect data, read-only, at `/staff/admin/`.

## Running it

### With Docker Compose

```sh
cp .env.example .env
# Fill in DJANGO_SECRET_KEY, FIELD_ENCRYPTION_KEY and OWNER_PASSWORD (instructions in the file).
docker compose up --build
```

Compose runs migrations, creates the cache table and runs `manage.py seed`, then serves on http://localhost:8000.

### Without Docker

Needs Python 3.12+ and PostgreSQL 16+.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # and fill it in; point DATABASE_URL at your database
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createcachetable   # the shared cache: request counting, TOTP replay protection
.venv/bin/python manage.py seed
.venv/bin/python manage.py runserver
```

### First sign-in

`manage.py seed` creates the seven roles, the four initial sub-forums, and the Owner account from
`OWNER_EMAIL` and `OWNER_PASSWORD`. It is idempotent: running it again creates only what is
missing and never overwrites settings or passwords. Sign in as the Owner and you are sent straight
to TOTP enrolment; nothing else is reachable until it is done. In development, sign-in emails are
printed to the console.

## Scheduled jobs

Run these once a day (cron, or the host's scheduler):

```sh
.venv/bin/python manage.py expire_invitations     # pending invitations past invitation.expiry_days
.venv/bin/python manage.py delete_ended_accounts  # invited accounts whose invitation ended unapproved
.venv/bin/python manage.py expire_actions         # mark time-limited moderation actions as ended
.venv/bin/python manage.py send_notification_emails  # the daily pointer email for chosen kinds
.venv/bin/python manage.py billing_daily          # renewal and lapse reminders, read-only on lapse, comp endings
.venv/bin/python manage.py sessions_daily         # end expired and idle sessions, delete old session records
```

Once, on the day billing goes live, an Owner runs `manage.py launch_billing`: comps granted before
then become founding comps ending a year later.

## Tests

```sh
.venv/bin/pytest
```

The tests need PostgreSQL (the audit log's append-only trigger and JSON settings are
Postgres-specific). The database user in `DATABASE_URL` needs permission to create the test
database. The tests do not read `.env`.

Tests must not use `transaction=True`: the audit table refuses `TRUNCATE`, which is how Django
resets the database between transactional tests.

## Layout

| App | Holds |
| --- | --- |
| `core` | Settings registry, permission service (`core/permissions.py`), access-control middleware, read-only staff admin, `seed` command, site settings, notifications, data requests |
| `accounts` | User, roles and role assignments, encrypted identity records, sessions |
| `sponsorship` | Invitations, sponsorships (the pedigree), promotions and eligibility |
| `boards` | Sub-forums, threads (including DMs), posts, revisions, quotes, attachments; rate limits and held posts; the Markdown renderer (`rendering.py`), image re-encoding (`images.py`), what each member can see (`visibility.py`) and the forum pages |
| `moderation` | Reports and automatic flags, moderation actions, sponsor reviews, DM access grants; the queue, the feed and the staff pages |
| `billing` | Stripe subscription and charge records |
| `audit` | Append-only audit log |

Authorisation goes through `can(actor, action, target)` in `core/permissions.py`; any action without
a rule is denied. Changes that need checking or auditing go through the `services.py` module of each
app, which checks permission and writes the audit entry in the same transaction.

## Configuration

Everything comes from environment variables, documented in [.env.example](.env.example). Never
commit `.env` or any secret. Tunable forum rules (sponsorship caps, rate limits, grace periods) are
not environment variables: they are registry entries in `core/registry.py` with defaults from the
design document, changed by an Owner through site settings or per sub-forum.

## Licence

[MIT](LICENSE).
