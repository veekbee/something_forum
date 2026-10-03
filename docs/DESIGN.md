# Forum Design Document

Oct 2, 2026 · @Victoria

## Purpose and design goals

A paid, invite-only discussion forum where every member is a verified human vouched for by another member, moderated by people, and closed to anyone who is not logged in. Quality of conversation is the product; scale is not a goal.

Non-negotiable requirements, in priority order:

1. Every member has a sponsor; the sponsorship graph (pedigree) is a first-class record.
2. Human moderation with a tiered trust ladder (Owner, Admin, Moderator, Tenured, Full, Provisional, Guest) whose rules differ per sub-forum.
3. vBulletin-style structure: sub-forums containing named threads containing posts, with per-sub-forum settings (rate limits, images on/off, who may read or post).
4. Zero public surface: no reading, searching, indexing or scraping without an authenticated session.
5. Full admin visibility: every post and DM is retained and searchable by Admins, and members are told so up front.
6. GDPR as the standard to aim for, not a legal obligation: handle personal data in the humane, member-respecting way GDPR describes, reconciled with point 5 through transparency and retention rules.
7. Paid membership with billing integrated into the trust ladder.
8. Reachable from any modern browser, designed mobile-first and installable to the home screen; no native or store-listed apps (decided 3 Oct 2026).

The implementation is open. The sections below pin down what each requirement means in practice, then compare adopting an existing forum platform against building on a web framework.

## User roles and trust ladder

Seven roles, ordered by trust. Promotion is a human decision (or a time-plus-conduct rule), never automatic on payment alone. The permissions below are a starting proposal to refine.

| Role | How obtained | Read | Post | Sponsor others | DM | Moderate |
| --- | --- | --- | --- | --- | --- | --- |
| Owner | Founder; transferable only by an Owner | All, incl. DMs and deleted content | Anywhere | Yes, unlimited; exempt from sponsorship rules | Yes | Everything an Admin can, plus appoint and remove Admins, change site settings, billing account and data-destroying operations |
| Admin | Appointed by Owner | All, incl. DMs and deleted content | Anywhere | Yes, unlimited; exempt from sponsorship rules | Yes | Full moderation and user admin; may act alone on any action |
| Moderator | Appointed by Admin from Tenured | All member sub-forums, mod queue | Anywhere | Yes, cap 7 active sponsorships | Yes | Edit, hide, lock, warn, suspend within assigned sub-forums; actions need a second approver |
| Tenured | Full for 3 months in good standing, promoted by Admin or Mod | All member sub-forums | Anywhere, rate-limited only where the sub-forum says so | Yes, cap 3 active sponsorships | Yes | None |
| Full | Provisional promoted after 3 months and 25 posts, on a Full+ recommendation, Moderator review and Admin approval | All member sub-forums | Anywhere, rate-limited only where the sub-forum says so | Yes, cap 1 active sponsorship (assumed; not yet confirmed) | Yes | None |
| Provisional | Sponsored, identity check passed, paid; in probation | All member sub-forums except those marked Full+ | Limited sub-forums, stricter rate limits, first 5 posts held for review | No | Only with sponsor and staff | None |
| Guest | Sponsored and identity-checked; has not paid | Guest Lobby and Introductions only | Guest Lobby and Introductions only, posts held for review | No | Only with sponsor and staff | None |

Design notes:

- Role is one axis; per-sub-forum permissions are a second axis (see Forum structure). A Full member may still be read-only in a sub-forum restricted to Tenured.
- A Moderator role is held per sub-forum or globally. Store it as a set of scopes, not a single flag. For posting permissions the role is per sub-forum (decided 3 Oct 2026): a member counts as a Moderator for a sub-forum's minimum roles and post holds only where they moderate.
- Owner exists so that later Admins can run the forum day to day without being able to remove the founder, alter site-wide settings, touch the billing account or destroy data. There may be more than one Owner, but only an Owner can create one.
- Admins and Owners are exempt from sponsorship rules (caps, transfer, and the sponsor review for a banned invitee) and may initiate and approve any moderation action alone. The record still shows that one person acted.
- Lapsed payment demotes to a read-only state rather than deleting the account, so the pedigree stays intact.
- Sponsorship caps, probation thresholds and grace periods are tunable settings, not code constants; defaults are in the Implementation brief.

## Sponsorship, pedigree and onboarding

Nobody creates their own account. A sponsor (Full or above, or the Admin) issues an invitation; the invitee passes an identity check; payment then moves them from Guest into probation. Every step is logged against both the sponsor and the new member.

&#91;embedded content: onboarding flow · 6 states, 1 check\]

A failed identity check ends the invitation and notifies the sponsor; a Provisional who fails probation is removed and the sponsor's record carries the outcome.

Promotion from Provisional to Full (decided 2 Oct 2026):

1. Qualifying stats: at least 3 months as Provisional and at least 25 posts (revised 3 Oct 2026; the earlier word-count alternative is dropped). The system shows eligibility automatically but promotes nobody on its own.
2. Recommendation: any member at Full or above puts the eligible Provisional forward. The sponsor may recommend, but need not be the one who does.
3. Moderator review: a Moderator signs off, adding notes where the record warrants them, for example any post holds, warnings, suspensions or bans incurred while Provisional.
4. Admin approval: the Admin does the final review and approves or declines. The decision, the recommender, the reviewing Moderator and the notes are all kept on the member's record.

Promotion from Full to Tenured: 3 months as Full in good standing, then promoted by an Admin or Moderator; no recommendation step.

Pedigree model:

- Each user record stores `sponsor_id`, `sponsored_at`, and the sponsor's role at the time. The pedigree is the tree formed by these links, rooted at the Owner.
- Pedigree is permanent. Removing a member leaves a tombstone so the tree does not break; see GDPR for what the tombstone may contain.
- Sponsorship is active until Tenured. A Tenured member no longer needs a sponsor; the original link stays in the pedigree as history only.
- Active sponsorship caps: Full 1 (assumed), Tenured 3, Moderator 7, Admin and Owner unlimited. The cap follows the member's highest unrevoked global role assignment (decided 3 Oct 2026), so a Tenured member who moderates one sub-forum keeps the Tenured cap.
- Pending invitations count toward the cap (decided 3 Oct 2026). A sponsor at cap may still invite, but each invitation holds a slot in the order it was sent, and an invitee who accepts when no slot is free is waitlisted until one frees up. Order is by sending, not by acceptance: a later invitee who accepts first is still waitlisted behind an earlier invitation that is pending (confirmed 3 Oct 2026). An Admin or Owner may approve a waitlisted invitation anyway, which in effect raises that sponsor's cap. Leadership is meant to have wide discretion here.
- If a sponsor leaves, is banned or loses sponsoring rights, each of their pre-Tenure invitees must be vouched for by another actively sponsoring member (a sponsorship transfer). Until that happens the invitee is read-only. Grace period to be set.
- Sponsors are accountable, but through human review rather than an automatic penalty (decided 3 Oct 2026). When a Guest or Provisional is banned, the system opens a sponsor review for an Admin or Owner, showing the sponsor's record and the outcomes of their other invitees. The reviewer chooses the response: no action, a warning, suspension of sponsoring privileges for a chosen number of months, or a ban. For a suspension, the reviewer also decides whether the sponsor's current pre-Tenure invitees stay with them or must be transferred. A ban can be reversed through payment of a flat $10 fee (set 3 Oct 2026; a setting, so it can change or scale by role later). Sponsors who are Admins or Owners get no review.
- Admins can view the full tree and search by sponsor; members see their own line up and down.

Identity check ("know your customer"), lightest workable version first:

1. Sponsor fills in a short vouching form: how they know the person and for how long.
2. Invitee supplies a real name (held privately), a verified email, and a verified phone or payment method.
3. Admin reviews and approves manually. At small scale this is the strongest check available.
4. No video call or third-party ID service at launch (decided 3 Oct 2026). Membership starts from a network the Admin knows personally, so manual Admin review is the check. Revisit once members are sponsoring people the Admin does not know, at which point a short video call with the sponsor or a Moderator is the next step up.

The check is for "is this a real, distinct person who someone here knows", not for legal identity. Keep the collected data minimal; it is the most sensitive data the forum holds.

## Forum structure and per-sub-forum rules

The content model is vBulletin's: Forum → Sub-forum (nestable) → Thread → Post. Each sub-forum carries its own rule set, so a single permission system must answer "may this role do this action in this sub-forum" for every request.

Per-sub-forum settings to support from day one:

| Setting | Values | Notes |
| --- | --- | --- |
| Minimum role to read | Guest … Admin | Guest-readable areas are still login-only |
| Minimum role to start a thread | Provisional … Admin | Separate from replying |
| Minimum role to reply | Provisional … Admin |  |
| Post rate limit | N posts per hour / day / week, per user | Applies to new posts; editing is separate |
| Thread rate limit | N new threads per period, per user | Prevents one member flooding a sub-forum |
| Images | Off / inline / attachments only | Also sets a per-post size cap |
| Links | Off / members-only / on | Off reduces spam risk in probation areas |
| Edit window | Minutes, or unlimited | Edits keep a history visible to staff |
| Posts held for review | Off / first N posts of a Provisional (default 5) / all | Feeds the moderation queue |
| Moderators | Set of users | Scope of the Moderator role |

Initial sub-forums (decided 3 Oct 2026). A deliberately minimal set; the escalating rate limits are what give each one its character, from conversation to considered argument to long-form writing.

| Sub-forum | Post rate limit | Purpose |
| --- | --- | --- |
| Guest Lobby and Introductions | Unlimited | The one area Guests can read and post in: Guests introduce themselves and meet members before paying; all members can read and reply |
| General Discussion | Unlimited | Everyday conversation |
| Serious Discussion | 1 post per user per day | Considered replies; the limit rewards thinking before posting |
| Seminars | 1 post per user per week | Long-form, essay-length contributions |

Rate-limit semantics (confirmed 3 Oct 2026): the limit counts every post, whether it starts a thread or replies to one, and runs on a rolling window measured from the member's earlier posts, not a calendar day or week. Other settings use the defaults in the Implementation brief until set per sub-forum. In the Guest Lobby, everyone from Guest up may read, start threads and reply, with no rate limit (decided 3 Oct 2026); Guests post under the same holds as a Provisional member (first posts reviewed, links off), so a newcomer's first words are seen by staff before the community.

Held posts (decided 3 Oct 2026): the first N count is site-wide, not per sub-forum. A held post counts as a post, both toward N and toward rate limits, until staff reject it; a rejected post is struck from both counts. A post the member deletes still counts, so deleting cannot be used to get around a rate limit. A held post is visible only to its author and to the staff who can release it (Moderators of that sub-forum, Admins and Owners). The author sees it in the thread, marked as held, and in the list of all their posts on their profile page. A rejected post disappears from the thread for everyone except staff, but stays in its author's post history on their profile, marked as rejected, and the author gets an in-app notification when it is rejected (decided 3 Oct 2026).

Per-role rate limits (decided 3 Oct 2026): a sub-forum can set a different post rate limit for particular roles, overriding its general limit. None are set at launch, so Provisional members have the same limits as Full members.

Thread and post behaviours:

- Threads are flat and chronological, with quote-reply. Nested threading is deliberately out of scope; it changes the character of discussion.
- Posts support rich text (Markdown or a limited HTML subset), quoting, and mentions. No reaction counters or vote scores by default; they shift incentives toward performance over conversation. Revisit later if members want them.
- Soft-delete only. Deleted posts stay visible to Admins with who deleted them and why.
- Thread states: open, locked, pinned, archived (read-only, excluded from "new posts"). A locked thread takes no new replies except from Admins and Owners, anywhere, and Moderators in sub-forums they moderate (decided 3 Oct 2026). An archived thread cannot be modified by anyone, Admins and Owners included: no replies, edits or deletions (confirmed 3 Oct 2026).
- Full-text search across threads and posts, scoped by what the searcher may read.

Direct messages are modelled as a private thread type between two or more members, so they reuse the same storage, search and retention machinery. Admin visibility of DMs is covered in the next section.

## Moderation and admin visibility

Moderation is done by people, so the tooling's job is to make human review fast and auditable, not to automate decisions.

Tools staff need:

- A moderation queue: held posts, member reports, and automatic flags (rate-limit hits, rapid deletions, new-member link posting) in one list, oldest first, with one-click approve, edit, hide or escalate.
- Per-member view: all posts, DMs, warnings, sponsor, invitees, payment status, and sessions on one page.
- Graduated actions: staff note (private), warning, post hold, posting suspension for a period, read-only, ban. An action by a Moderator is initiated by one staff member and approved by a second, and a ban's approver must be an Admin. An Admin or Owner may initiate and approve any action alone (confirmed 3 Oct 2026); the record shows a single actor in that case. A staff note needs no approver: it takes effect at once, notifies every Admin and Owner, and appears in the Mod feedback feed (decided 3 Oct 2026). Staff act only on members ranked below them: a Moderator cannot act on another Moderator, and only an Owner can act on an Admin (decided 3 Oct 2026).
- Full-text search across everything, including DMs and soft-deleted content, for Admins. Moderators search only sub-forums in their scope and no DMs unless the Admin grants it.
- An immutable audit log of every staff action. Admins can read it; nobody can edit it.

Disciplinary record (decided 2 Oct 2026): every action except the private staff note is published to a record visible to all members at Provisional and above. Each entry shows the member, the action, the Mod or Admin who initiated it, the Mod or Admin who approved it, and the date. Reasons are shown in summary form chosen by the approver. The public record is a view over the audit log, not a separate store, so the two cannot drift apart.

Admin visibility of DMs is a stated term of membership, shown at signup and in the member agreement, and repeated in the DM interface. The forum does not offer end-to-end encryption, and the UI must never imply it. Moderators read DMs only under an Admin grant (decided 3 Oct 2026): each grant names the Moderator, the members or case it covers, and an expiry, and both the grant and every DM a Moderator opens under it are written to the audit log.

Sponsor accountability lives here too. Warnings and removals of a member are visible on the sponsor's record. A banned Guest or Provisional opens a sponsor review in the moderation queue for an Admin or Owner to decide (see Sponsorship). A ban is lifted only through the reversal payment or an Admin decision. Every ban, sponsor review decision, suspension of sponsoring privileges, transfer of sponsorship and reversal is written to the audit log.

## Access control and anti-scraping

The forum has no anonymous surface at all. Every URL except the login page, the invitation-acceptance page, and legal notices requires an authenticated session. This removes most scraping risk by construction; what remains is member-side leakage and credential abuse.

Baseline:

- Authentication (confirmed 3 Oct 2026): email and password, with a second factor (TOTP authenticator app) required for every account before it can read anything. Recovery codes issued at setup; a lost second factor is reset by an Admin after confirming identity with the member's sponsor. Whoever controls an account's verified email address is, in effect, its owner (decided 3 Oct 2026): a forgotten password is reset through a link sent to a verified address on the account, never an unverified one, and the reset does not bypass TOTP. A member who cannot get back in may also send an appeal for Admins to read.
- Deny-by-default routing: the server returns 401 or redirects to login for any unauthenticated request, including static assets that reveal content (attachments, avatars).
- `robots.txt` disallows everything; `X-Robots-Tag: noindex, nofollow` on every response; no sitemap, no RSS, no public API.
- Attachments served through authenticated, short-lived signed URLs, never from a public bucket.
- No email digests containing post bodies by default (email is an uncontrolled copy). Notifications link back to the forum.

Against members scraping or sharing credentials:

- Session binding: sessions tied to device fingerprint and rough geography; a second concurrent location prompts re-authentication and alerts staff.
- Per-member request rate limits well above human reading speed but below bulk download. Trip the limit and the account is read-only until a Moderator clears it.
- Pagination with no "show all" view; thread and search results capped per page.
- Optional light watermarking: each member's rendered pages carry an invisible per-session marker (zero-width characters or spacing) so a leaked copy identifies its source. Cheap and effective at small scale.

What this cannot do: stop a paying member from photographing a screen or retyping a post. The sponsorship model and the member agreement are the real defence there; the technical measures raise the cost and make leaks traceable.

## Privacy, GDPR and data retention

The forum is operated and hosted in the US (decided 3 Oct 2026). GDPR is adopted as an ideal rather than a compliance requirement: it is a well-worked-out description of treating members' data with respect, so the forum follows it wherever doing so is practical. Two caveats keep this honest. First, GDPR can reach a non-EU operator that offers its service to people in the EU, so if EU residents are ever admitted, some of these positions become obligations rather than choices; following them from the start means nothing has to change. Second, US law still applies on its own terms, chiefly the state data-breach notification laws, which cover every state. This section is a design position, not legal advice; have a lawyer review the member agreement and privacy notice before launch.

Positions:

- Lawful basis: performance of contract for running the service; legitimate interest (community safety, fraud prevention) for staff visibility into content and DMs; consent for anything optional. State each in the privacy notice.
- Data minimisation: identity-check data (real name, phone) is stored separately from the forum profile, encrypted at rest, visible only to Admins, and deleted or reduced once the member reaches Full.
- Access requests: an export of the member's posts, DMs, profile and identity data, generated by a button in their settings, delivered within the 30-day window.
- Erasure requests: posts are anonymised rather than deleted (author replaced by a tombstone, content kept, since other members' replies depend on it) unless the member asks for full removal of specific posts. Identity data is deleted. The pedigree keeps a tombstone node with only the sponsor link and dates, no personal data. This is the standard "forum post" approach and is defensible, but document the reasoning.
- DMs: both parties' copies are retained while either remains a member; on erasure, the requester's side is anonymised. Admin search continues to work on retained content.
- Retention: audit logs and moderation records kept for a fixed period after a member leaves (proposed: 2 years), then deleted. Deleting audit entries needs a deliberate path around the audit log's append-only trigger; its design is deferred (3 Oct 2026).
- Hosting: US region. Keep a short list of every processor that touches member data (host, Stripe, email sender) with what each holds, and prefer ones that publish a Data Processing Agreement, so the list is ready if EU members ever join.
- Breach handling: a written one-page procedure. Aim for GDPR's 72-hour notification standard, which also satisfies the US state breach-notification deadlines.

## Platforms: browser only, installable

The forum is a responsive web application and nothing else (decided 3 Oct 2026). There is no native app, no store-listed wrapper and no desktop app. Pages are designed mobile-first, so a phone is a first-class way to use the forum rather than a reduced one.

The site is installable to the home screen (decided 3 Oct 2026). It ships a web app manifest and icons, so an installed copy opens full-screen like an app, and a service worker, which browsers require for installability and which is what lets an installed site receive push notifications on iPhones. The service worker caches only the static shell (CSS, JavaScript, icons, and a static offline page with no member content). It never caches forum pages, posts, DMs or attachments, so no member content is stored on the device beyond what the browser itself keeps. A service worker cannot see a logout, so the logout response carries a `Clear-Site-Data: "cache", "storage"` header, which empties the shell cache and unregisters the service worker; it registers again on the next visit (clarified 3 Oct 2026).

| Platform | How members use it | Why |
| --- | --- | --- |
| Phone and tablet | The website in the browser, or installed to the home screen | Covers iOS and Android with one codebase; installed, it looks and launches like an app |
| Desktop | The website in the browser | A forum gains nothing from a desktop wrapper |

Why no apps:

- Invitation is the only way in, so an app store listing's discoverability is worth nothing here.
- Payment stays entirely on the web, which keeps App Store billing rules out of the picture.
- No app review of a forum full of user-written content, and no store content policies to satisfy.
- One rendering path keeps the anti-scraping measures (session binding, signed attachment URLs, watermarking) consistent, and there is no separate app API to protect.
- Offline reading, the main thing a native app could add, is unwanted: copies of posts on members' devices work against the login-only design.

Push notifications are possible through the installed site but are not yet designed; see Still open.

## Payments and billing

Billing is a recurring subscription managed by a payment provider (Stripe or Paddle class), with the forum reacting to webhooks rather than storing card data. The provider's hosted checkout and customer portal handle cards, VAT and invoices; the forum stores only a customer reference and a subscription status.

How billing meets the trust ladder:

- Payment is a precondition for Provisional, not for Guest. A Guest can read and post in the Guest Lobby and Introductions before paying.
- Lapsed payment sets the account to read-only after a grace period (14 days, confirmed) and demotes nothing else; role and pedigree are kept. Renewal restores the previous role.
- Admin can comp or discount individual members (founding members, Moderators) without touching the role system.
- Pricing, trial periods and whether a sponsor can gift a first month are product decisions for a later session.

Provider: Stripe, with Stripe Tax turned on (decided 3 Oct 2026, operator in the US). For a small, mostly-US membership Stripe's fees are lower than Paddle's, its Python SDK and webhooks fit the Django build directly, and Stripe Tax calculates state sales tax on subscriptions and tracks how close the forum is to each state's registration threshold. The operator stays responsible for registering and filing where required, which at small scale may be nowhere or only the home state. Paddle, which acts as merchant of record and takes on all tax filing for a higher fee, becomes worth a second look if international membership grows. Confirm current fees with both before launch.

## Existing tools: adopt, extend or build

No existing platform has the sponsorship pedigree, sponsor accountability or per-member watermarking; every option needs custom code for those. The question is whether to bolt them onto a mature forum engine or to build the engine around them. The four requirements that filter the field hardest are: login-required everywhere, per-sub-forum rate limits, admin-searchable DMs, and a pedigree.

| Platform | Stack, licence | Login-only mode | Per-forum rate limits | Admin reads DMs | Invite tracks inviter | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| vBulletin | PHP, commercial | Yes | Flood control is global; per-forum needs add-on | No, needs add-on | No | The reference design, but the current product is widely regarded as the weakest of the commercial options; reviews cite slow development and migration away |
| [XenForo](https://xenforomobile.app/what-is-the-best-forum-software-in-2026-an-honest-comparison/) | PHP, commercial (\~$195 one-time, mid-2026) | Yes ("Guest" group can be denied everything) | Partial; per-node permissions, flood limit global | No, needs add-on | Via add-ons | Closest look and structure to vBulletin; where most vBulletin communities went; large add-on market |
| [Discourse](https://blog.elest.io/discourse-vs-flarum-vs-nodebb-which-self-hosted-forum-platform-in-2026/) | Ruby on Rails + Ember, open source | Yes, built in ("login required") | Per-category slow mode; per-user limits by trust level | Yes, built in | Yes, invites record who invited | Best moderation tooling of the open-source options; trust levels resemble the ladder here; GDPR export built in. Flat, infinite-scroll presentation differs most from vBulletin and needs theming |
| [Flarum](https://www.ssdnodes.com/learn/self-hosted-forum-software) | PHP/Laravel, open source | Yes | Via extensions | Via extension | Via extension | Lightest to run; smaller core, so more of the list above is extension-dependent |
| NodeBB | Node.js + MongoDB/Redis, open source | Yes | Via plugins | Via plugin | No | Real-time focus is not what this forum needs; adds a MongoDB dependency |
| Misago | Python/Django + React, open source | Yes | Category-level permissions only | No | No | The one Python-native forum. Activity is a single maintainer and releases are infrequent; the PyPI package has not been updated in over two years. Usable as a reference codebase more than a product |
| Custom build | Python/Django + PostgreSQL, HTMX or React front end | By design | By design | By design | By design | Everything above becomes a model and a view you own. Cost is time to reach parity on basics (editor, search, notifications, attachments) |

Three realistic paths:

1. Adopt Discourse in login-required mode and write one plugin for the pedigree, sponsor caps and sponsor accountability. Fastest route to a working, moderated, GDPR-capable forum; most of this document is already built. The cost is accepting Discourse's presentation and a Ruby codebase for the custom parts.
2. Adopt XenForo and assemble the missing pieces from add-ons plus a small custom add-on in PHP. Looks and behaves most like vBulletin out of the box. The cost is a commercial licence, PHP for custom work, and no native DM visibility for Admins.
3. Build on Django. The pedigree, trust ladder and per-forum rules become the core of the data model rather than an add-on, the stack matches the Python preference, and the UI can be exactly the vBulletin form wanted. The cost is roughly three to four months of one developer's time before members can use it (an estimate, not a quote), and every later feature is also yours to build.

Components worth reusing in path 3 regardless: PostgreSQL full-text search (or Meilisearch), django-allauth for authentication and TOTP, Stripe hosted checkout and customer portal, a Markdown renderer with a strict allow-list, and object storage with signed URLs for attachments.

## Recommended direction

Decision (2 Oct 2026): build on Django, path 3. The Discourse trial is dropped; XenForo stays on record only as a fallback if the build stalls.

Why this path fits: the pedigree, sponsorship transfer and sponsor penalties are core to the data model rather than add-ons; the stack matches the Python preference; and the interface can follow the vBulletin form exactly. Proposed build order:

1. Data model and permission engine: roles, sub-forum rules, pedigree, bans and transfers. Everything else is a query against this.
2. Authentication, invitations and the onboarding flow, including Admin manual review and the Provisional-to-Full promotion workflow.
3. Sub-forums, threads, posts, editor, soft-delete, edit history, search.
4. Moderation queue, audit log, per-member view, DM threads with Admin search.
5. Billing webhooks, read-only on lapse, ban-reversal payment.
6. Anti-scraping layer, mobile-first responsive UI, home-screen installability (manifest, icons, shell-only service worker).

Stack (confirmed 3 Oct 2026): Django with PostgreSQL, HTMX for the server-rendered UI (keeps one rendering path for the anti-scraping measures), django-allauth, a strict Markdown renderer, object storage with signed URLs, and PostgreSQL full-text search to start.

The platform-independent decisions in this document (roles, onboarding and promotion rules, sponsorship rules, per-sub-forum settings, GDPR positions, billing behaviour) stand as the specification for the build.

## Implementation brief

This section, together with the rest of the document, is the first prompt for a Claude Code session. It fixes the stack, defines the data model, lists every tunable setting with its default, and states the rules the code must enforce that are easy to miss.

### Stack (confirmed 3 Oct 2026)

- Python 3.12 or later, Django 5.x, PostgreSQL 16 or later.
- Server-rendered templates with HTMX for interactivity; one rendering path, no separate front-end build.
- django-allauth for email/password login and its MFA module for required TOTP.
- Stripe Python SDK with hosted Checkout and Customer Portal; webhooks drive subscription state.
- django-storages against S3-compatible object storage, signed URLs only.
- PostgreSQL full-text search to start; swap for Meilisearch only if needed.
- pytest-django, Docker Compose for local development, a single `manage.py seed` command that creates roles, the Owner account and the four initial sub-forums.

### Data model

Design choices, each made for extensibility: roles are rows and assignments, not a column on the user, so new roles or per-sub-forum scopes need no migration; sub-forum and site settings are key/value pairs validated against a registry in code, so a new setting is one registry entry; DMs are threads, so they share search, retention and moderation; history is kept in separate tables (revisions, sponsorships, assignments) rather than overwritten; nothing is hard-deleted except on an erasure request; the audit log is append-only at the database level.

| Entity | Key fields | Notes |
| --- | --- | --- |
| User | email (unique), password, display\_name, slug, status (invited, guest, active, read\_only, suspended, banned, removed, tombstone), joined\_at, last\_seen\_at | Login identity and public profile only. Password and TOTP live in allauth's tables. Tombstone keeps the row with personal fields cleared |
| Role | name, rank (int), is\_staff | Seeded: owner 70, admin 60, moderator 50, tenured 40, full 30, provisional 20, guest 10. Rank comparisons drive "minimum role" checks |
| RoleAssignment | user, role, scope\_subforum (nullable), granted\_by, granted\_at, revoked\_at, revoked\_by, reason | Trust level = highest unrevoked global assignment. Moderator scopes set scope\_subforum. Never update a row to change a role; revoke and add |
| IdentityRecord | user (1:1), real\_name (encrypted), phone (encrypted), email\_verified\_at, phone\_verified\_at, vouching\_notes, reviewed\_by, reviewed\_at, reduced\_at | Separate table with its own permission check so identity data never rides along with profile queries. reduced\_at marks the GDPR-style minimisation after promotion to Full |
| Invitation | sponsor, invitee\_email, token\_hash (the token itself is only emailed), vouching\_notes, status (pending, waitlisted, approved, declined, expired), created\_at, decided\_by, decided\_at | Admin manual review happens here. Approval creates the User (status guest), IdentityRecord and first Sponsorship. Pending invitations count toward the sponsor's cap; a waitlisted one waits for a free slot or an Admin or Owner approval over the cap |
| Sponsorship | sponsor, member, sponsor\_role (at the time), started\_at, ended\_at, end\_reason (tenured, transferred, sponsor\_left, sponsor\_banned, member\_removed), previous (self FK), is\_original | Active sponsorship = ended\_at null. Pedigree = the is\_original rows. A transfer closes one row and opens another pointing back at it. Cap checks count a sponsor's active rows |
| Promotion | member, from\_role, to\_role, recommended\_by, reviewed\_by, review\_notes, decided\_by, status (recommended, reviewed, approved, declined), timestamps | Eligibility is computed, not stored. The row is the workflow record |
| SubForum | parent (self FK, nullable), name, slug, description, position, is\_archived, settings (JSONB) | settings validated against the registry below; missing keys fall back to site defaults |
| Thread | subforum (nullable for DMs), kind (discussion, dm), title, author, state (open, locked, archived), is\_pinned, created\_at, last\_post\_at, post\_count | DM threads have no subforum and are visible only to participants, Admins, Owners, and Moderators under a grant |
| ThreadParticipant | thread, user, joined\_at, last\_read\_at | DM membership and per-user read position. Also used for thread subscriptions |
| Post | thread, author, body\_source, body\_html, created\_at, edited\_at, is\_held, released\_by, released\_at, rejected\_by, rejected\_at, deleted\_at, deleted\_by, delete\_reason | Index on (author, created\_at) serves the rolling-window rate limits, which are computed from this table rather than stored. Rejected posts are excluded from rate-limit and held-post counts; deleted posts are not. body\_html is rendered server-side through a strict allow-list and never re-rendered on read |
| PostRevision | post, body\_source, edited\_by, edited\_at | Written on every edit; visible to staff |
| Attachment | post, uploader, storage\_key, filename, mime, size\_bytes, width, height, created\_at | Served only via signed URL after a permission check on the post |
| Report | post (nullable), user (nullable), reporter, reason, created\_at, status, handled\_by, handled\_at | Feeds the moderation queue alongside held posts and automatic flags |
| ModerationAction | target\_user, kind (note, warning, hold, suspension, read\_only, ban, ban\_reversal, sponsorship\_transfer, sponsoring\_suspension), initiated\_by, approved\_by (nullable), status (pending, active, expired, reversed), starts\_at, ends\_at, internal\_reason, public\_summary, is\_public, related\_post, related\_action | sponsoring\_suspension removes the right to sponsor until ends\_at. is\_public false only for kind note. A note is active on creation with no approver. Public record = query over is\_public rows |
| SponsorReview | banned\_member, sponsor, triggering\_action, status (pending, decided), outcome (no\_action, warning, sponsoring\_suspension, ban), suspension\_months, invitees\_transfer (bool), resulting\_action, decided\_by, decided\_at, notes, created\_at | Opened in the same transaction as an approved ban on a Guest or Provisional whose active sponsor is not Admin or Owner. Decided only by Admin or Owner |
| DMAccessGrant | moderator, granted\_by, subject\_users (M2M), case\_note, expires\_at, revoked\_at | Every DM read under a grant writes an AuditEntry |
| Subscription | user (1:1), stripe\_customer\_id, stripe\_subscription\_id, status (none, active, past\_due, lapsed, comped), current\_period\_end, read\_only\_at | read\_only\_at = period end + lapse grace. A job flips status to read\_only when it passes |
| Charge | user, kind (subscription, ban\_reversal), stripe\_payment\_intent\_id, amount\_cents, currency, status, related\_action, created\_at | A successful ban\_reversal charge sets the related ModerationAction to reversed |
| UserSession | user, session\_key, device\_fingerprint, ip\_prefix, approx\_location, user\_agent, created\_at, last\_seen\_at, revoked\_at, watermark\_seed | Session binding and the per-session watermark both key off this row |
| Notification | recipient, kind, payload (JSONB), created\_at, read\_at | In-app first; email only as a pointer back to the forum |
| AuditEntry | actor, action, target\_type, target\_id, payload (JSONB), ip, created\_at | Append-only: a PostgreSQL rule or trigger rejects UPDATE and DELETE, and the Django model has no save path for existing rows |
| SiteSetting | key, value (JSONB), updated\_by, updated\_at | Only Owners write. Keys and defaults come from the registry in code |
| DataRequest | user, kind (export, erasure), requested\_at, status, file\_key, completed\_at, handled\_by | Export produces a zip via signed URL; erasure runs the anonymisation described in the Privacy section |

### Settings registry and defaults

Every number below is a registry entry with a default; Owners change site-wide values in SiteSetting, and sub-forum keys can be overridden per sub-forum. Nothing here is a code constant.

| Key | Default | Scope | Status |
| --- | --- | --- | --- |
| sponsorship.cap.full | 1 | site | Assumed, confirm |
| sponsorship.cap.tenured | 3 | site | Confirmed |
| sponsorship.cap.moderator | 7 | site | Confirmed |
| sponsorship.cap.admin, .owner | unlimited | site | Confirmed |
| sponsorship.transfer\_grace\_days | 30 | site | Proposed |
| sponsorship.review\_on\_member\_ban | true | site | Confirmed 3 Oct 2026; opens a sponsor review when a Guest or Provisional is banned; Admin and Owner sponsors exempt |
| promotion.full.min\_days | 90 | site | Confirmed (3 months) |
| promotion.full.min\_posts | 25 | site | Confirmed |
| promotion.tenured.min\_days | 90 | site | Confirmed (3 months as Full) |
| provisional.held\_posts | 5 | site, overridable per sub-forum | Confirmed |
| billing.lapse\_grace\_days | 14 | site | Confirmed |
| billing.ban\_reversal\_fee\_cents | 1000 ($10) | site | Confirmed; flat for all roles |
| auth.require\_totp | true | site | Confirmed |
| retention.audit\_years\_after\_departure | 2 | site | Proposed |
| scraping.requests\_per\_10\_min | 600 | site | Proposed; tune from real traffic |
| subforum.min\_read\_role | provisional | sub-forum | Guest Lobby sets guest |
| subforum.min\_thread\_role | provisional | sub-forum | Guest Lobby sets guest |
| subforum.min\_reply\_role | provisional | sub-forum | Guest Lobby sets guest |
| subforum.post\_rate\_limit | none | sub-forum | Serious Discussion 1 per 24h; Seminars 1 per 7d; rolling window |
| subforum.post\_rate\_limit\_by\_role | {} (no overrides) | sub-forum | Decided 3 Oct 2026; maps role to a limit that replaces post\_rate\_limit for that role; none set at launch |
| subforum.thread\_rate\_limit | none | sub-forum |  |
| subforum.images | off | sub-forum |  |
| subforum.links | members\_only | sub-forum | Guest Lobby sets off |
| subforum.edit\_window\_minutes | 30 | sub-forum | Proposed |
| subforum.hold\_posts | first\_n\_provisional | sub-forum | Guest Lobby also holds Guest posts |

### Rules the code must enforce

1. Deny by default. One permission service, `can(actor, action, target)`, answers every authorisation question from account status, role rank, sub-forum settings and rate limits. Views never check roles directly.
2. Admin and Owner bypass the two-person rule on moderation actions; the ModerationAction row records a single actor. Everyone else needs a distinct approver, and a ban's approver must be Admin or Owner. Staff notes need no approver and notify every Admin and Owner. Staff act only on members ranked below them.
3. Admin and Owner are exempt from sponsorship caps, transfer requirements and sponsor review. They can still appear as sponsors in the pedigree. For everyone else the cap comes from the highest unrevoked global role assignment, and an active sponsoring\_suspension removes the right to sponsor.
4. Only an Owner can create or remove an Owner or Admin, write SiteSetting, or run data-destroying operations (erasure, hard purge).
5. Role changes are new RoleAssignment rows, never updates. Sponsorship changes are new Sponsorship rows, never updates.
6. Rate limits are computed from Post timestamps over a rolling window; thread starts count as posts, held posts count, rejected posts do not. A per-role limit in the sub-forum replaces the general limit for that role.
7. A Provisional's first N posts site-wide (per provisional.held\_posts, excluding rejected posts) are created with is\_held true and, until released, are visible only to their author and to staff who can release them. Guest posts in the Guest Lobby follow the same path.
8. Admin and Owner can read any thread including DMs; Moderators read a DM only under an unexpired DMAccessGrant covering a participant, and each read is audited.
9. Every request from an unauthenticated session is refused except login, password reset, invitation acceptance, Stripe webhooks, legal pages, and the web app manifest, its icons and the service worker script, which carry no member content. No TOTP, no session. Password reset mail goes only to verified addresses.
10. AuditEntry rows are written inside the same database transaction as the action they record, and the table rejects updates and deletes.
11. Posts are soft-deleted only. Erasure anonymises per the Privacy section and is the one path that clears personal fields.
12. A ban on a Guest or Provisional opens a SponsorReview in the same transaction, unless the sponsor is Admin or Owner. Nothing happens to the sponsor until an Admin or Owner decides the review.
13. Locked threads accept replies only from Admins, Owners and Moderators of that sub-forum; archived threads accept no changes from anyone. A rejected post is shown to its author only in their post history, never in the thread. Moderator rank counts toward a sub-forum's minimum roles only where the member moderates.
14. The service worker caches only static shell assets, including one static offline page that contains no member content. It never caches server-rendered pages, HTMX fragments or attachments. The logout response sends `Clear-Site-Data: "cache", "storage"`.

### First Claude Code session: milestone 1

Scope: repository scaffold, data model, permission service, seed data, tests. No member-facing UI beyond login and TOTP enrolment; Django admin is enough for the Owner to inspect data.

Definition of done:

- Docker Compose brings up Django and PostgreSQL; `manage.py seed` creates the seven roles, an Owner account, and the four initial sub-forums with the settings in this document.
- Migrations for every entity in the Data model table, with the audit-log append-only rule applied in a migration.
- Settings registry in code with the defaults above; SubForum.settings and SiteSetting validate against it.
- Permission service with tests covering: role rank versus sub-forum minimums, Provisional held posts, rolling-window rate limits for all three limited sub-forums, Admin-alone moderation actions, sponsor cap enforcement, sponsor review on ban with Admin exemption, sponsoring suspension, DM visibility with and without a grant.
- allauth configured for email/password with mandatory TOTP; an account without TOTP cannot reach any forum view.
- A short README describing how to run it and pointing back to this document.

Later milestones follow the build order in Recommended direction.

### Repository

Public (decided 3 Oct 2026), at [github.com/veekbee/something\_forum](https://github.com/veekbee/something_forum). Expected layout: this document at `docs/DESIGN.md` and session guidance at `CLAUDE.md` in the root. The value is the community, not the code, and a readable codebase is a security asset rather than a liability if the usual hygiene holds: no secrets in the repository (environment variables only, with a committed `.env.example`), secret scanning and dependency alerts switched on, a security policy file, and prompt patching of dependencies. One design consequence: the watermarking scheme becomes public, so its parameters (which characters, which positions) must live in configuration, not code, and the scheme should be treated as a tracing aid rather than a secret. Licence: MIT (decided 3 Oct 2026).

Change workflow (decided 3 Oct 2026): this file is the single authority for the design. Design discussions happen in Claude.ai chats, which read the repository but do not commit to it. They propose changes as a unified diff against this file (and against `CLAUDE.md` where session guidance changes), and the implementation session in Claude Code reviews the patch, applies it, makes any code changes it implies, and commits. A patch that conflicts with the current file, or with code already built, is sent back with the conflict described rather than merged by guesswork. The Claude.ai design doc that preceded this file is kept as history only.

## Open questions for later sessions

Decided on 2 and 3 Oct 2026 and written into the sections above: platform (Django), sponsorship caps and transfer, probation thresholds, identity-check depth, Moderator DM access, payment provider (Stripe), hosting and jurisdiction (US, GDPR as an ideal), initial sub-forums, authentication, repository visibility, Guest Lobby posting and rate limit, per-role rate limits, held-post counting, sponsor caps for scoped Moderators, sponsor review in place of the automatic sponsor ban, held-post visibility, staff notes without approval, staff acting only on lower ranks, invitation waitlist, replies in locked threads, archived threads, rejected-post visibility, password reset, code licence (MIT), platforms (browser only, installable to the home screen).

Still open:

- [ ] Pricing, trial period, and whether a sponsor can gift a first month.
- [ ] Sponsorship cap for Full members (default 1 is assumed).
- [ ] Whether a member may voluntarily change sponsor, outside the forced transfer when a sponsor leaves.
- [ ] What fails probation, beyond a ban: for example a warning count or a Moderator recommendation.
- [ ] Proposed defaults awaiting confirmation: transfer grace period 30 days, edit window 30 minutes, audit retention 2 years, scraping threshold 600 requests per 10 minutes.
- [ ] What the Mod feedback feed shows, and who reads it.
- [ ] How a locked-out member sends an appeal to Admins: a public form adds an unauthenticated page; an email address does not.
- [ ] Deleting audit entries at the end of the retention period, given the append-only trigger (deferred 3 Oct 2026).
- [ ] Web push notifications: whether to offer them, for which events, and what a notification may contain (a push message passes through Apple's or Google's servers, so no post content).
- [ ] Legal review of the member agreement and privacy notice before any member joins.

## Sources

Comparison pages opened for the tooling section, as of October 2026: [Discourse vs Flarum vs NodeBB (elest.io)](https://blog.elest.io/discourse-vs-flarum-vs-nodebb-which-self-hosted-forum-platform-in-2026/), [Self-hosted forum software compared (SSD Nodes)](https://www.ssdnodes.com/learn/self-hosted-forum-software), [Best forum software in 2026 (xenforomobile.app)](https://xenforomobile.app/what-is-the-best-forum-software-in-2026-an-honest-comparison/). Platform feature claims in the table are from working knowledge of each product and were not independently verified; they matter only if the build stalls and the XenForo fallback is revisited.
