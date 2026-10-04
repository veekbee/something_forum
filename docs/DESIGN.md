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
| Moderator | Appointed by Admin from Tenured | All member sub-forums, mod queue; DMs only under an Admin grant | Anywhere | Yes, cap 7 active sponsorships | Yes | Edit, hide, lock, warn, suspend within assigned sub-forums; actions need a second approver |
| Tenured | Full for 3 months in good standing, promoted by Admin or Mod | All member sub-forums | Anywhere, rate-limited only where the sub-forum says so | Yes, cap 3 active sponsorships | Yes | None |
| Full | Provisional promoted after 3 months and 25 posts, on a Full+ recommendation, Moderator review and Admin approval | All member sub-forums | Anywhere, rate-limited only where the sub-forum says so | Yes, cap 1 active sponsorship (assumed; not yet confirmed) | With Full and above; with Guests and Provisionals only as their sponsor or staff | None |
| Provisional | Sponsored, identity check passed, paid or comped; in the Provisional period | All member sub-forums except those marked Full+ | Limited sub-forums, stricter rate limits, first 5 posts held for review | No | Only with sponsor and staff | None |
| Guest | Sponsored and identity-checked; has not paid | Guest Lobby and Introductions only | Guest Lobby and Introductions only, posts held for review | No | Only with sponsor and staff | None |

Design notes:

- Role is one axis; per-sub-forum permissions are a second axis (see Forum structure). A Full member may still be read-only in a sub-forum restricted to Tenured.
- A Moderator role is held per sub-forum or globally. Store it as a set of scopes, not a single flag. For posting permissions the role is per sub-forum (decided 3 Oct 2026): a member counts as a Moderator for a sub-forum's minimum roles and post holds only where they moderate.
- Owner exists so that later Admins can run the forum day to day without being able to remove the founder, alter site-wide settings, touch the billing account or destroy data. There may be more than one Owner, but only an Owner can create one.
- Admins and Owners are exempt from sponsorship rules (caps, transfer, and the sponsor review for a banned invitee) and may initiate and approve any moderation action alone. The record still shows that one person acted.
- Lapsed payment demotes to a read-only state rather than deleting the account, so the pedigree stays intact. No account is ever removed for lapsing (see Payments and billing).
- Sponsorship caps, Provisional-period thresholds and grace periods are tunable settings, not code constants; defaults are in the Implementation brief.

## Sponsorship, pedigree and onboarding

Nobody creates their own account. A sponsor (Full or above, or the Admin) issues an invitation; the invitee passes an identity check; payment, or a complimentary membership granted by an Admin or Owner, then moves them from Guest into the Provisional period. Every step is logged against both the sponsor and the new member.

Onboarding flow (confirmed 3 Oct 2026). The path is invitation, identity check, Guest, Provisional, Full, Tenured. The one check is the Admin's manual review. The account is created when the invitee accepts, not when they are approved (decided 3 Oct 2026), so every onboarding page after acceptance sits behind a signed-in, TOTP-verified session.

| From | Event | Who | To |
| --- | --- | --- | --- |
| (nothing) | Sends an invitation and fills in the vouching form | Sponsor (Full or above, Admin or Owner) | Invitation pending |
| Pending | Follows the emailed link and sets a password | Invitee | Account created with status invited. Invitation accepted if it has a slot in the sponsor's send order, otherwise waitlisted |
| Pending | 14 days pass without acceptance | System | Invitation expired; its slot frees |
| Accepted or waitlisted | Enrols TOTP, gives their real name, submits | Invitee | Same status, now ready for review |
| Waitlisted | A slot frees and this invitation is next in send order | System | Accepted |
| Accepted, ready for review | Approves | Admin or Owner | Invitation approved; account becomes Guest; first Sponsorship recorded |
| Waitlisted, ready for review | Approves over the cap | Admin or Owner | As above |
| Accepted or waitlisted, ready for review | Declines (the identity check fails) | Admin or Owner | Invitation declined; sponsor told |
| Any status before approval | Declines the invitation | Invitee | Invitation invitee-declined; sponsor told |
| Any status before approval | Rescinds the invitation | Sponsor | Invitation rescinded |
| Guest | Pays, accepts a first year gifted by their sponsor, or is given a complimentary membership | Stripe webhook; Admin or Owner | Provisional |
| Provisional | Passes the promotion workflow below | Recommender, Moderator, Admin | Full |
| Full | 3 months in good standing, then promoted | Admin or Moderator | Tenured |

Every ending before approval (declined, invitee-declined, rescinded, expired) frees the invitation's slot. Once the invitee has accepted, their invitation does not expire (decided 3 Oct 2026); it waits for the review, the sponsor or the invitee, and the review queue shows how long each has waited. An account whose invitation ends without approval can no longer sign in and is deleted, with its identity details, after 30 days (proposed), which leaves room to reverse a mistaken decline. The invitation record is kept, so the history shows who invited whom and how it ended.

The complimentary path (decided 3 Oct 2026) lets an Admin or Owner move a Guest to Provisional without payment, recorded as a comped subscription. It exists before billing is built and stays afterwards for founding members, Moderators and anyone else leadership chooses to comp.

A Provisional who fails the Provisional period is removed and the sponsor's record carries the outcome.

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
- Sponsors are accountable, but through human review rather than an automatic penalty (decided 3 Oct 2026). When a Guest or Provisional is banned, the system opens a sponsor review for an Admin or Owner, showing the sponsor's record and the outcomes of their other invitees. The reviewer chooses the response: no action, a warning, suspension of sponsoring privileges for a chosen number of months, or a ban. For a suspension, the reviewer also decides whether the sponsor's current pre-Tenure invitees stay with them or must be transferred. Every ordinary ban can be reversed through payment: $10 the first time, doubling with each ban the member has already paid off (decided 3 Oct 2026). A Permanent Ban cannot (see Moderation). Sponsors who are Admins or Owners get no review.
- Admins can view the full tree and search by sponsor; members see their own line up and down.

Identity check ("know your customer"), lightest workable version first:

1. Sponsor fills in a short vouching form: how they know the person and for how long.
2. Invitee supplies a real name (held privately). Following the emailed invitation link verifies the email address, and the account's email stays fixed to that address until approval. No phone number is collected at launch (decided 3 Oct 2026): a verified email and the Admin's review are the whole check, because members have strong reasons to be careful about whom they invite.
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
| Images | Off / inline / attachments | Inline places uploaded images within the post; attachments shows them as thumbnails beneath it. Sets the per-post image cap |
| Links | Off / full\_and\_above / on | Governs outside links only; links within the forum always work. Off reduces spam risk in areas for newcomers |
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
- Posts support rich text in Markdown, quoting, and mentions. No reaction counters or vote scores by default; they shift incentives toward performance over conversation. Revisit later if members want them.
- Soft-delete only. Deleted posts stay visible to Admins with who deleted them and why. In the thread a deleted post keeps its place as a placeholder reading "Deleted by" and the name of the staff member who deleted it, or "Deleted by the author"; its text is gone for readers (decided 3 Oct 2026). Rejected posts were never published and leave no placeholder.
- Thread states: open, locked, pinned, archived (read-only, excluded from "new posts"). A locked thread takes no new replies except from Admins and Owners, anywhere, and Moderators in sub-forums they moderate (decided 3 Oct 2026). An archived thread cannot be modified by anyone, Admins and Owners included: no replies, edits or deletions (confirmed 3 Oct 2026). The two exceptions are an Owner unarchiving a thread and Admin redaction in the Thread Graveyard, both below (decided 3 Oct 2026).
- Full-text search across threads and posts, scoped by what the searcher may read.

Markdown allow-list (decided 3 Oct 2026): paragraphs and line breaks, bold and italic, strikethrough, bulleted and numbered lists, block quotes, inline code and code blocks, horizontal rules, and two levels of headings, rendered small, for essay-length posts. No tables, since they read badly on a phone. Raw HTML is shown as plain text, never rendered and never silently stripped. Links and images follow the next two paragraphs.

Links (decided 3 Oct 2026): links to pages inside the forum always work. The links setting governs outside links only. With `off` they show as plain text; with `full_and_above` (the default, renamed from members-only) they are clickable when the author is Full or above and plain text from Guests and Provisionals; with `on` they are clickable from everyone. The Guest Lobby keeps `off`. Every outside link carries `rel="nofollow noopener noreferrer"`, and there is no "leaving the forum" warning page.

Images (decided 3 Oct 2026): images from other websites are never loaded, because loading one would tell that site who is reading and when; a pasted image address is an ordinary outside link. Only images uploaded to the forum appear, in JPEG, PNG, WebP or GIF, with no SVG and no other file types at launch. The server re-encodes every upload, which strips camera metadata such as location and neutralises malformed files. A sub-forum allows at most 4 images per post and 5 MB per image before re-encoding (proposed).

Mentions (decided 3 Oct 2026): `@` followed by the member's slug, with autocomplete. A mention notifies the member only if they can read the thread; otherwise it links to their profile and sends nothing. Held posts notify on release, rejected posts never, and invited accounts cannot be mentioned. At most 10 mentions in a post send notifications (proposed).

Quotes (decided 3 Oct 2026): a quote carries the author, a link to the original post and the quoted excerpt, and may only come from the same thread or DM, so copied text never crosses a permission boundary. If the original is edited, the quote keeps the words replied to and shows an "edited since" note. If the original is deleted, the quoted text is replaced by "quoted post deleted" for everyone. Staff read the original words in the quoting post's source and revision history, because a post's rendered text is shared by every reader (corrected 3 Oct 2026). Held and rejected posts cannot be quoted.

Editing and deleting (confirmed 3 Oct 2026): authors edit and delete their own posts only within the edit window and never in a locked thread. Staff edit and delete any post in sub-forums they moderate, at any time and without a second approver; deleting someone else's post needs a reason, and a post edited by staff shows an "edited by staff" note. A thread's starter may edit its title within the edit window of their first post, staff at any time, and earlier titles are kept for staff. Moderators move threads between sub-forums they moderate; Admins and Owners move them anywhere. A held post does not count as new thread activity until it is released.

Thread endings (decided 3 Oct 2026). A thread can end in three ways:

| Ending | What it means | Who | Readable by |
| --- | --- | --- | --- |
| Archived in place | Read-only in its own sub-forum | Admin or Owner | The sub-forum's readers |
| Thread Graveyard | What deleting a thread means; the thread is kept for good | Moderators in their sub-forums, Admins, Owners, with a reason | Provisional and above |
| Thread Classics | An honour for a thread of especially high quality that has reached its end | Admin or Owner | Provisional and above |

The Graveyard and the Classics are two read-only areas on the forum index, and a thread in either shows the sub-forum it came from. Because both are readable by Provisional and above whatever a thread's origin, sending a thread there can widen its audience; the confirmation step says so when it does, as does a move between sub-forums. DMs cannot be sent to either. An Admin may edit the title of a Graveyard thread when it was deleted for something that should be removed. Redacting offending text is a staff duty, done as soon as possible (decided 3 Oct 2026): Moderators redact posts in sub-forums they moderate, including Graveyard threads that came from them, and Admins and Owners anywhere. Each redacted post shows a "redacted by staff" note, and an Owner can purge the earlier versions from the revision history when the removed text must not survive anywhere. An Owner can unarchive a thread or bring one back from the Graveyard or the Classics, and every one of these actions is audited. The starter of a Classics thread has it listed under "Thread Classics" on their profile.

Profiles (decided 3 Oct 2026) show join date, role, Thread Classics honours, the avatar and caption (see Payments and billing) and a Rap Sheet link to the member's public disciplinary record. They show no post count, which would reward volume; members see their own count privately, beside their progress toward promotion.

Page sizes (confirmed 3 Oct 2026): 20 posts per thread page, 30 threads per sub-forum page, 20 search results per page, 20 posts per page of a member's post history. Layout follows the vBulletin structure adapted mobile-first: a forum index listing sub-forums with thread and post counts and the latest post, then the Graveyard and the Classics; a sub-forum page listing threads; a flat thread page. Pages are built plain for now, and the visual look is decided in a later design pass (decided 3 Oct 2026).

Direct messages are modelled as a private thread type between two or more members, so they reuse the same storage, search and retention machinery. Admin visibility of DMs is covered in the next section.

Direct messages (decided 3 Oct 2026):

| Topic | Rule |
| --- | --- |
| Who may message whom | Full and above message any member at Full or above. Guests and Provisionals message, and are messaged by, only their own sponsor and staff; a Full member who is not their sponsor cannot reach them. Staff means Admins, Owners, and the Moderators of any sub-forum the member can read |
| Restricted members | A read-only member (lapsed payment or Probation) or a suspended member messages only their sponsor and staff. A banned member messages only Admins and Owners, which is how a ban is appealed. Existing conversations with anyone else stay readable but take no new messages |
| Group conversations | At most 8 participants (proposed). Every pair in a conversation must be allowed to message each other. Any participant can add someone, and the conversation shows who added whom. A newcomer sees only messages from when they joined. Any participant can leave and keeps read-only access to what was said until then; they can be added back. Members cannot remove each other; Admins and Owners can (confirmed 3 Oct 2026) |
| Content | Outside links follow `full_and_above`. Images as in forum posts, with the same caps. Edit and delete within a 30-minute window, every version kept; deletion is soft, so Admins still see the message. No reply limit; at most 10 new conversations per member per day (proposed). DM messages are never held |
| Counting | DM messages are not posts for any count: not the 25 posts for promotion, not the first held posts, not any sub-forum rate limit |
| Blocking | A member can block another member, who then cannot start a DM with them, add them to a group, or notify them with a mention; an existing one-to-one conversation stops taking messages from the blocked side. Groups are unaffected, and the blocker may leave. The blocked member is not told. Staff cannot be blocked. Admins and Owners can see blocks. Blocking hides nobody's forum posts |
| Read position | last\_read\_at drives the member's own unread count only; nobody sees when others have read a message |
| Visibility notice | At the top of every conversation: "Direct messages are private from other members, but not from the forum's Admins. Admins can read and search every message, including edited and deleted ones. Moderators can read messages only with an Admin's permission, which is recorded. There is no end-to-end encryption." Under every compose box, linking to that text: "Admins can read all messages." |
| Reporting | A participant can report a DM message. DM reports go to Admins and Owners only; an Admin who wants a Moderator to handle one grants access in the usual way |

Notifications (decided 3 Oct 2026): a notifications page, with an unread count in the header. Email is only a pointer: it never carries post text, DM text or who wrote, only that something is waiting. Account emails always go out (invitation and onboarding steps, approval or decline, actions taken on the member's account, billing problems). Following a thread (decided 3 Oct 2026): every thread has a Follow / Unfollow button; members automatically follow threads they start and can unfollow them, and replying does not follow a thread. Followers get an in-app notification of new visible replies, except from members they have blocked. Emails for new DMs, mentions, replies in followed threads and promotion news are each off by default and chosen by the member; several are combined into at most one email a day.

## Moderation and admin visibility

Moderation is done by people, so the tooling's job is to make human review fast and auditable, not to automate decisions.

Tools staff need:

- A moderation queue: one list of everything waiting on staff, oldest first, with approve, edit, hide or escalate. Detailed below.
- Per-member view: posts, DMs, moderation history, sponsor, invitees, blocks, payment status and sessions on one page, in two tiers detailed below.
- Graduated actions: staff note (private), warning, post hold, posting suspension for a period, Probation (site-wide read-only, the disciplinary status), ban. An action by a Moderator is initiated by one staff member and approved by a second, and a ban's approver must be an Admin. An Admin or Owner may initiate and approve any action alone (confirmed 3 Oct 2026); the record shows a single actor in that case. A staff note needs no approver: it takes effect at once, notifies every Admin and Owner, and appears in the Mod feedback feed (decided 3 Oct 2026). Staff act only on members ranked below them: a Moderator cannot act on another Moderator, and only an Owner can act on an Admin (decided 3 Oct 2026).
- Full-text search across everything, including DMs and soft-deleted content, for Admins and Owners. Moderators search only sub-forums in their scope, and the DMs of members covered by a grant they hold.
- An immutable audit log of every staff action. Admins and Owners can read it; nobody can edit it.

Disciplinary record (decided 2 Oct 2026): every action except the private staff note is published to a record visible to all members at Provisional and above. Each entry shows the member, the action, the Mod or Admin who initiated it, the Mod or Admin who approved it, and the date. Reasons are shown as a short public summary: the initiator drafts it, the approver may edit it before approving, and both versions are kept (decided 3 Oct 2026). The public record is a query over the moderation actions marked public, and every change to one is audited in the same transaction, so the record and the audit log cannot drift apart (corrected 3 Oct 2026). Declined and withdrawn actions never appear on it.

The Rap Sheet (decided 3 Oct 2026) is that record's page, linked from every profile: all public actions, newest first (warnings, suspensions, Probation, bans, Permanent Bans), each with its date, summary, scope, initiator and approver. An entry links to the offending post when there is one, under the reader's normal permissions, so it never shows a post the reader could not otherwise see. A deleted or hidden post shows as removed by staff, a redacted post shows its redacted text, and an offence in a DM reads "in a private message" with no link.

Permanent Ban (decided 3 Oct 2026). Distinct from a ban, it applies to the person, not the account, and cannot be bought back. Only an Owner imposes one; Admins and Moderators can raise a case by escalation. The account can no longer sign in: an attempt shows a single page saying it is permanently banned. The account and its posts stay for the record. The person's verified email addresses and real name go on a permanent-ban list, visible only to Admins and Owners, and every new invitation is checked against it; a match is shown to the reviewing Admin, not acted on automatically, and an account confirmed to be the same person is Permanently Banned too, with a sponsor review if the sponsor knew. An Owner can annul a Permanent Ban, with a written reason, audited and shown as annulled; as policy this is done only to correct an error, never as forgiveness.

Admin visibility of DMs is a stated term of membership, shown at signup and in the member agreement, and repeated in the DM interface. The forum does not offer end-to-end encryption, and the UI must never imply it. Moderators read DMs only under an Admin grant (decided 3 Oct 2026): each grant names the Moderator, the members or case it covers, and an expiry, and both the grant and every DM a Moderator opens under it are written to the audit log. Admin and Owner DM reads are audited the same way, and a search whose results show DM text counts as a read, writing one entry that lists the conversations shown (decided 3 Oct 2026). Members are not told when their DMs were read; the notice already says they can be.

Sponsor accountability lives here too. Warnings and removals of a member are visible on the sponsor's record. A banned Guest or Provisional opens a sponsor review in the moderation queue for an Admin or Owner to decide (see Sponsorship). A ban is lifted only through the reversal payment or an Admin decision. Every ban, sponsor review decision, suspension of sponsoring privileges, transfer of sponsorship and reversal is written to the audit log.

The moderation queue (decided 3 Oct 2026) holds every item waiting on staff, with filters by type and each item's waiting time:

| Item | Who sees it |
| --- | --- |
| Held posts | Moderators of that sub-forum, Admins, Owners |
| Reports about a post | Moderators of that sub-forum, Admins, Owners |
| Automatic flags on a post | Moderators of that sub-forum, Admins, Owners |
| Reports about a member, with no post involved | Global Moderators, Admins, Owners |
| Moderator actions awaiting a second approver | Staff who may approve them; for a ban, Admins and Owners |
| Promotions awaiting Moderator review | Moderators, Admins, Owners |
| Sponsor reviews | Admins and Owners |
| DM reports | Admins and Owners |
| Request-rate flags | Moderators, Admins, Owners |

A global Moderator is one whose Moderator role is not limited to particular sub-forums. Hiding a post is a soft delete with a reason chosen from a preset list (off-topic, personal attack, spam, private information, or other with a sentence); the author is notified with the reason, and the post stays in their history marked as removed. A Moderator can escalate any item to Admins and Owners with a note. A report or flag is itself marked escalated; escalating any other item (a held post, a pending action, a promotion awaiting review) creates a report of kind escalation that points at it (decided 3 Oct 2026). Either way it stays in the queue marked escalated, Moderators can still see it and add notes, only an Admin or Owner can resolve it, and the escalating Moderator hears the outcome. Items are not claimed: whoever acts first wins, and each action checks the item is still open, so a second Moderator sees "already handled" and nothing changes.

Reports (decided 3 Oct 2026): every member, Guests included, can report posts, DM messages they take part in, and members (for a pattern rather than one post), with a reason from the same preset list and an optional note. The reported member never learns who reported them; staff see the reporter. The reporter hears the outcome in one line, "action taken" or "no action needed". Each member may make at most 10 reports a day (proposed), and reporting the same thing twice adds nothing.

Automatic flags (decided 3 Oct 2026) go into the same queue: 3 posts refused by rate limits within 24 hours; 5 of a member's own posts deleted by them within 60 minutes; and any trip of the request-rate limit, which also makes the account read-only until a Moderator clears it. All thresholds are proposed. A flag for new members posting outside links is not used, since those links already show as plain text.

Moderation actions (decided 3 Oct 2026):

- An approver may decline a pending action, with a short reason sent to the initiator; the initiator may withdraw their own action before approval. Neither appears on the public record.
- A Moderator's suspension or post hold covers only the sub-forums they moderate; one action can name several of them (decided 3 Oct 2026). The same action site-wide, and Probation, which is always site-wide, need an Admin or Owner as initiator or approver, as a ban does. Warnings and staff notes attach to the member. The public record shows where a limited action applies.
- A time-limited action stops applying at its end time whenever permissions are checked; a daily job then marks it expired so the record reads correctly.
- A ban does not change the account's status or roles; a member counts as banned while a ban action is in force. Lifting the ban, by payment or by an Admin, therefore returns them to exactly where they were. The public record keeps the ban, marked lifted with the date, and does not say how. A sponsor banned after a sponsor review is treated the same way.
- "Probation" names only this disciplinary status. Read-only because of a lapsed payment is shown as "read-only (lapsed)".

Staff views (decided 3 Oct 2026):

- The per-member view has two tiers. Admins and Owners see everything. Moderators see the member's posts in sub-forums they moderate (held and rejected included), moderation history, sponsor and invitees, but not DMs, blocks, payment, sessions or the real name (blocks corrected 3 Oct 2026). For Admins and Owners the real name sits behind a "show identity details" control, and each use is audited; opening the view itself is not.
- The audit log view is for Admins and Owners, filtered by actor, member concerned, action and date range, newest first, with payloads shown in full. An audit entry never contains a real name or DM text; it refers to them by id.
- The Mod feedback feed is shared awareness among staff, distinct from the audit log (the complete record, Admins and Owners) and notifications (personal). It shows staff notes, escalations and how they were resolved, declined and withdrawn actions with their reasons, and sponsor review decisions, each linked to its source. All staff read it; Moderators see only items about sub-forums they moderate or about members, and anything involving a DM is for Admins and Owners. Routine approvals and releases are left out.

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
- Per-member request rate limits well above human reading speed but below bulk download. Trip the limit and the account is read-only until a Moderator clears it, with a flag in the moderation queue.
- Pagination with no "show all" view; thread and search results capped per page.
- Optional light watermarking: each member's rendered pages carry an invisible per-session marker (zero-width characters or spacing) so a leaked copy identifies its source. Cheap and effective at small scale.

What this cannot do: stop a paying member from photographing a screen or retyping a post. The sponsorship model and the member agreement are the real defence there; the technical measures raise the cost and make leaks traceable.

## Privacy, GDPR and data retention

The forum is operated and hosted in the US (decided 3 Oct 2026). GDPR is adopted as an ideal rather than a compliance requirement: it is a well-worked-out description of treating members' data with respect, so the forum follows it wherever doing so is practical. Two caveats keep this honest. First, GDPR can reach a non-EU operator that offers its service to people in the EU, so if EU residents are ever admitted, some of these positions become obligations rather than choices; following them from the start means nothing has to change. Second, US law still applies on its own terms, chiefly the state data-breach notification laws, which cover every state. This section is a design position, not legal advice; have a lawyer review the member agreement and privacy notice before launch.

Positions:

- Lawful basis: performance of contract for running the service; legitimate interest (community safety, fraud prevention) for staff visibility into content and DMs; consent for anything optional. State each in the privacy notice.
- Data minimisation: identity-check data (real name, phone) is stored separately from the forum profile, encrypted at rest, visible only to Admins, and deleted or reduced once the member reaches Full.
- Access requests: an export of the member's posts, DMs, profile and identity data, generated by a button in their settings, delivered within the 30-day window.
- Erasure requests: the permanent-ban list survives erasure, keeping only a Permanently Banned person's verified email addresses and real name, as the forum's legitimate interest in keeping them out; the privacy notice says so. Otherwise, posts are anonymised rather than deleted (author replaced by a tombstone, content kept, since other members' replies depend on it) unless the member asks for full removal of specific posts. Identity data is deleted. The pedigree keeps a tombstone node with only the sponsor link and dates, no personal data. This is the standard "forum post" approach and is defensible, but document the reasoning.
- DMs: both parties' copies are retained while either remains a member; on erasure, the requester's side is anonymised. Admin search continues to work on retained content.
- Retention: audit logs and moderation records kept for a fixed period after a member leaves (proposed: 2 years), then deleted. Deleting audit entries needs a deliberate path around the audit log's append-only trigger; its design is deferred (3 Oct 2026).
- Hosting: US region. Keep a short list of every processor that touches member data (host, Stripe, email sender) with what each holds (Stripe holds each payer's name, billing address and card details, collected by Checkout for Stripe Tax), and prefer ones that publish a Data Processing Agreement, so the list is ready if EU members ever join.
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
- Lapsed payment sets the account to read-only after a grace period (14 days, confirmed) and demotes nothing else. The member sees it as "read-only (lapsed)", never as Probation; role and pedigree are kept. Renewal restores the previous role.
- Admins and Owners comp individual members without touching the role system.

Membership (decided 3 Oct 2026): $25 a year in US dollars, with no monthly plan and no free trial; the Guest period serves as one. The price lives in Stripe and the forum refers to it by its Stripe price id, set in configuration, so changing a price needs no deploy. A Guest can pay at any time after approval, and payment moves them to Provisional at once, starting the Provisional period.

Comps (decided 3 Oct 2026):

- Owners, Admins and Moderators are comped automatically while they hold a staff role. When someone leaves staff the staff comp ends and the normal lapse rules apply from that moment, unless an Admin or Owner keeps them comped as a deliberate choice.
- Comps granted before billing launches are founding comps and end one year after launch; the member is emailed 30 days before. An Owner can extend any member's comp.
- Comps granted for other reasons are untouched by either rule.

Lapsing and renewal (decided 3 Oct 2026):

- The forum's own clock governs, whatever Stripe's retry schedule is doing: the 14-day grace starts at the end of the paid year or comp, and read-only follows. A payment at any point restores the member at once.
- Stripe's retries run inside the grace period. When the member goes read-only the forum cancels the Stripe subscription, so returning means a fresh Checkout and a new year from that day, never paying for time away. Within the grace, fixing the card pays the renewal and keeps the yearly date.
- Cancelling in the Customer Portal stops the next renewal; the member keeps full access to the end of the paid year, then the same 14 days apply.
- For the first 90 days of a lapse (proposed) the member is read-only (lapsed): they read every sub-forum whose setting allows lapsed readers (all of them at first) and post nothing. After that their access narrows to their billing page, their own post history, and DMs with their sponsor and staff, and they count as a sponsor who has left, so their pre-Tenure sponsees go through sponsorship transfer. No account is ever removed for lapsing.
- While lapsed, existing sponsorships and invitations continue, but the member sends no new invitations and recommends no promotions, and a Provisional's 90-day clock pauses.
- Payment restores everything at once: status active, the role they held, and a running Provisional clock.

Ban payments (decided 3 Oct 2026): every ordinary ban can be lifted by payment. The fee is $10, doubling with each ban the member has already paid off ($10, $20, $40). A banned member can reach one extra page, the ban payment page, which shows the fee and sends them to Checkout. Payment lifts the ban only; any suspension or Probation in force carries on. The Rap Sheet shows the ban as lifted without saying how; staff see in the audit log and the per-member view that it was paid, and how much.

Gifts (decided 3 Oct 2026): a sponsor can pay for their own invitee's first year once the invitee is an approved Guest. The gift counts as the Guest's payment and moves them to Provisional when the Guest accepts it. It is a one-off: the renewal is the member's own, set up when they first pay for themselves, and the sponsor's card is never attached to another member's subscription.

Paid extras (decided 3 Oct 2026) are sold from a catalogue, so a new extra is a catalogue entry rather than new code. The first is the custom avatar and caption, $10 paid once: without it a member has a generated avatar (initials on a colour) and no caption; with it they upload an avatar image (handled like post images: re-encoded, metadata stripped, no SVG) and set a plain-text caption of at most 40 characters shown under their name, changeable at will. Extras are for Provisional and above, survive a lapse, and are not refunded if staff reset an offending avatar or caption to the default; repeated misuse can cost the member the extra through a moderation action.

Discounts: comping is enough at launch; Stripe promotion codes can be switched on later without a design change. Stripe sends its receipts; its failed-payment and renewal emails are turned off, because the forum sends its own.

Billing emails are account emails, always sent, as pointers with no content: a renewal reminder 30 days before each annual renewal (some US states, California among them, require advance notice of automatic renewal), payment failed, read-only in 3 days, now read-only (lapsed), payment restored, ban lifted, and a founding comp ending in 30 days. The billing page, showing status, period end, any read-only date and links to Checkout or the Customer Portal, is visible only to the member; staff see payment status in the per-member view.

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
5. Billing: annual membership, webhooks, lapse stages, comps, ban payments, Permanent Ban, gifts and paid extras.
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
- Django's database cache as the cache shared by every server process: it holds the request-rate counter and makes allauth's TOTP replay protection work across processes.
- pytest-django, Docker Compose for local development, a single `manage.py seed` command that creates roles, the Owner account and the four initial sub-forums.

### Data model

Design choices, each made for extensibility: roles are rows and assignments, not a column on the user, so new roles or per-sub-forum scopes need no migration; sub-forum and site settings are key/value pairs validated against a registry in code, so a new setting is one registry entry; DMs are threads, so they share search, retention and moderation; history is kept in separate tables (revisions, sponsorships, assignments) rather than overwritten; nothing is hard-deleted except on an erasure request; the audit log is append-only at the database level.

| Entity | Key fields | Notes |
| --- | --- | --- |
| User | email (unique), password, display\_name, slug, status (invited, guest, active, read\_only, suspended, banned, removed, tombstone), joined\_at, last\_seen\_at, avatar (Attachment, nullable), caption (nullable, 40 characters) | Login identity and public profile only. Password and TOTP live in allauth's tables. Tombstone keeps the row with personal fields cleared. Status invited covers the time between accepting an invitation and approval; such an account reaches only its onboarding pages. Bans do not set a status: a member counts as banned while a ban action is in force |
| Role | name, rank (int), is\_staff | Seeded: owner 70, admin 60, moderator 50, tenured 40, full 30, provisional 20, guest 10. Rank comparisons drive "minimum role" checks |
| RoleAssignment | user, role, scope\_subforum (nullable), granted\_by, granted\_at, revoked\_at, revoked\_by, reason | Trust level = highest unrevoked global assignment. Moderator scopes set scope\_subforum. Never update a row to change a role; revoke and add |
| IdentityRecord | user (1:1), real\_name (encrypted), phone (encrypted), email\_verified\_at, phone\_verified\_at, vouching\_notes, submitted\_at, reviewed\_by, reviewed\_at, reduced\_at | Separate table with its own permission check so identity data never rides along with profile queries. Created at acceptance; email\_verified\_at is set then, since the invitation link proves the address. submitted\_at marks the record ready for review. The phone fields stay empty at launch and exist so phone checks can be added without a migration. reduced\_at marks the GDPR-style minimisation after promotion to Full |
| Invitation | sponsor, invitee\_email, token\_hash (the token itself is only emailed), vouching\_notes, invitee (User, nullable), status (pending, accepted, waitlisted, approved, declined, invitee\_declined, rescinded, expired), created\_at, accepted\_at, decided\_by, decided\_at | Admin manual review happens here. Acceptance creates the User (status invited) and IdentityRecord and sets invitee. Approval moves the User to guest, assigns the guest role and creates the first Sponsorship. decided\_by and decided\_at record whoever ended or approved the invitation: an Admin or Owner, the sponsor (rescinded), the invitee (invitee\_declined), or nobody (expired). Expiry is computed from created\_at and the setting, not stored. Slot rules are in Rules the code must enforce |
| Sponsorship | sponsor, member, sponsor\_role (at the time), started\_at, ended\_at, end\_reason (tenured, transferred, sponsor\_left, sponsor\_banned, member\_removed), previous (self FK), is\_original | Active sponsorship = ended\_at null. Pedigree = the is\_original rows. A transfer closes one row and opens another pointing back at it. Cap checks count a sponsor's active rows |
| Promotion | member, from\_role, to\_role, recommended\_by, reviewed\_by, review\_notes, decided\_by, status (recommended, reviewed, approved, declined), timestamps | Eligibility is computed, not stored. The row is the workflow record |
| SubForum | parent (self FK, nullable), name, slug, description, position, is\_archived, kind (regular, graveyard, classics), settings (JSONB) | settings validated against the registry below; missing keys fall back to site defaults. Exactly one graveyard and one classics sub-forum exist, created by seed, read-only, readable by Provisional and above |
| Thread | subforum (nullable for DMs), kind (discussion, dm), title, author, state (open, locked, archived), is\_pinned, created\_at, last\_post\_at, post\_count, origin\_subforum (nullable), ended\_by, ended\_at, end\_reason | DM threads have no subforum and are visible only to participants, Admins, Owners, and Moderators under a grant. A thread in the Graveyard or the Classics is archived and keeps origin\_subforum. last\_post\_at ignores held posts until release |
| ThreadTitleRevision | thread, title, edited\_by, edited\_at | Written on every title change; visible to staff |
| ThreadParticipant | thread, user, joined\_at, last\_read\_at, added\_by, left\_at | DM membership and per-user read position. On a discussion thread, a row records that the member follows it: created automatically for the thread's starter, and by the Follow button for anyone else. In a DM, a participant sees messages from joined\_at up to left\_at; last\_read\_at is shown to nobody else |
| Block | blocker, blocked, created\_at | One row per block; unique on the pair. Staff cannot be blocked |
| Post | thread, author, body\_source, body\_html, created\_at, edited\_at, is\_held, released\_by, released\_at, rejected\_by, rejected\_at, deleted\_at, deleted\_by, delete\_reason | Index on (author, created\_at) serves the rolling-window rate limits, which are computed from this table rather than stored. Rejected posts are excluded from rate-limit and held-post counts; deleted posts are not. body\_html is rendered server-side through a strict allow-list and never re-rendered on read |
| PostRevision | post, body\_source, edited\_by, edited\_at, is\_redaction, purged\_by, purged\_at | Written on every edit; visible to staff. An edit by anyone other than the author drives the "edited by staff" note, and a Graveyard redaction the "redacted by staff" note. An Owner purge clears body\_source on earlier revisions and records who purged them |
| PostQuote | quoting\_post, quoted\_post | One row per quote. When a quoted post is edited, deleted or anonymised, the posts quoting it are re-rendered |
| Attachment | post, uploader, storage\_key, filename, mime, size\_bytes, width, height, created\_at | Images only, stored after server-side re-encoding. Served only via signed URL after a permission check on the post |
| Report | source (member, system), kind (post, member, dm, escalation, flag\_rate\_limit, flag\_rapid\_deletion, flag\_request\_rate), post (nullable), user (nullable), related\_action (ModerationAction, nullable), related\_promotion (Promotion, nullable), reporter (nullable for system flags), reason (preset), note, details (JSONB), status (open, escalated, resolved), escalated\_by, escalation\_note, outcome (action\_taken, no\_action), handled\_by, handled\_at, created\_at | Member reports and automatic flags in one table, so the queue reads one list. A dm report has post set to a DM message and is visible only to Admins and Owners. An escalation report is created already escalated and points at its item through post, related\_action or related\_promotion; resolving it does not by itself release, approve or decide that item |
| ModerationAction | target\_user, kind (note, warning, hold, suspension, probation, ban, permanent\_ban, ban\_reversal, sponsorship\_transfer, sponsoring\_suspension), scope\_subforums (sub-forums, many-to-many; empty means site-wide; set only for a limited suspension or hold), initiated\_by, approved\_by (nullable), declined\_by, decline\_reason, status (pending, active, expired, reversed, declined, withdrawn, annulled), starts\_at, ends\_at, internal\_reason, public\_summary\_draft, public\_summary, is\_public, related\_post, related\_action | Kind probation was read\_only before 3 Oct 2026. A reversed ban shows on the public record as lifted. Declined and withdrawn actions are never public. public\_summary\_draft is the initiator's text and public\_summary the published one. sponsoring\_suspension removes the right to sponsor until ends\_at. is\_public false only for kind note. A note is active on creation with no approver. Public record = query over is\_public rows |
| SponsorReview | banned\_member, sponsor, triggering\_action, status (pending, decided), outcome (no\_action, warning, sponsoring\_suspension, ban), suspension\_months, invitees\_transfer (bool), resulting\_action, decided\_by, decided\_at, notes, created\_at | Opened in the same transaction as an approved ban on a Guest or Provisional whose active sponsor is not Admin or Owner. Decided only by Admin or Owner |
| DMAccessGrant | moderator, granted\_by, subject\_users (M2M), case\_note, expires\_at, revoked\_at | Every DM read under a grant writes an AuditEntry |
| Subscription | user (1:1), stripe\_customer\_id, stripe\_subscription\_id, status (none, active, past\_due, lapsed, comped), current\_period\_end, cancel\_at\_period\_end, read\_only\_at, restricted\_at, comped\_by, comped\_at, comp\_reason (staff, founding, other), comped\_until (nullable) | read\_only\_at = end of the paid period or comp + lapse grace; restricted\_at = read\_only\_at + billing.lapse\_restrict\_days. A job flips the account to read\_only when read\_only\_at passes and cancels the Stripe subscription. A comp has no Stripe ids; a staff comp has no comped\_until and ends with the staff role; a founding comp's comped\_until is one year after billing launches. A gifted first year sets current\_period\_end without a Stripe subscription |
| LapsePeriod | user, started\_at, ended\_at (nullable) | One row per lapse. The Provisional clock subtracts lapsed days, and the restriction stage is measured from started\_at |
| Charge | user, kind (subscription, ban\_reversal, extra, gift), stripe\_payment\_intent\_id, amount\_cents, currency, status, related\_action, created\_at | A successful ban\_reversal charge sets the related ModerationAction to reversed; its amount is computed when the payment page opens from the base fee and the number of the member's earlier paid-off bans |
| StripeEvent | event\_id (unique), type, received\_at, processed\_at | Each webhook event is processed once, in the same transaction as its effects. Handled: checkout completed (subscriptions and one-time payments), invoice paid, invoice payment failed, subscription updated, subscription deleted; all others are recorded and ignored |
| Gift | sponsor, invitee, charge, status (paid, accepted), created\_at, accepted\_at | Only a sponsor for their own approved Guest invitee. Acceptance moves the Guest to Provisional |
| Extra | key, name, stripe\_price\_setting, min\_role, is\_active | The catalogue of paid extras. The first is avatar\_caption. The price id comes from configuration named by stripe\_price\_setting |
| Entitlement | user, extra, charge, granted\_at, revoked\_at, revoked\_by\_action (ModerationAction, nullable) | What a member has bought. One-time and permanent unless revoked by a moderation action |
| PermanentBanRecord | action (ModerationAction), emails (encrypted list), real\_name (encrypted), created\_at, annulled\_at | The permanent-ban list. Visible only to Admins and Owners, checked on every invitation, kept through erasure |
| UserSession | user, session\_key, device\_fingerprint, ip\_prefix, approx\_location, user\_agent, created\_at, last\_seen\_at, revoked\_at, watermark\_seed | Session binding and the per-session watermark both key off this row |
| Notification | recipient, kind, payload (JSONB), created\_at, read\_at, emailed\_at | In-app first; email only as a pointer back to the forum, carrying no content or sender |
| NotificationPreference | user, kind, email (bool) | One row per member and optional kind; missing rows mean off. Account kinds always email and have no row |
| AuditEntry | actor, action, target\_type, target\_id, payload (JSONB), ip, created\_at | Payloads never contain a real name or DM text, only ids. Append-only: a PostgreSQL rule or trigger rejects UPDATE and DELETE, and the Django model has no save path for existing rows |
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
| invitation.expiry\_days | 14 | site | Confirmed 3 Oct 2026; applies only to invitations not yet accepted |
| invitation.ended\_account\_deletion\_days | 30 | site | Proposed; days before an invited account whose invitation ended without approval is deleted |
| billing.ban\_reversal\_fee\_cents | 1000 ($10) | site | Confirmed; base fee for the first paid-off ban |
| billing.ban\_reversal\_fee\_multiplier | 2 | site | Confirmed 3 Oct 2026; applied once per earlier paid-off ban |
| billing.lapse\_restrict\_days | 90 | site | Proposed; days of read-only (lapsed) before access narrows |
| billing.renewal\_reminder\_days | 30 | site | Confirmed 3 Oct 2026 |
| billing.read\_only\_warning\_days | 3 | site | Confirmed 3 Oct 2026 |
| billing.founding\_comp\_months | 12 | site | Confirmed 3 Oct 2026; from billing launch |
| billing.founding\_comp\_warning\_days | 30 | site | Confirmed 3 Oct 2026 |
| extras.caption\_max\_chars | 40 | site | Confirmed 3 Oct 2026 |
| subforum.readable\_when\_lapsed | true | sub-forum | Confirmed 3 Oct 2026; whether read-only (lapsed) members can read it |
| auth.require\_totp | true | site | Confirmed |
| retention.audit\_years\_after\_departure | 2 | site | Proposed |
| scraping.requests\_per\_10\_min | 600 | site | Proposed; tune from real traffic |
| dm.max\_participants | 8 | site | Proposed |
| dm.max\_new\_conversations\_per\_day | 10 | site | Proposed; per member |
| dm.links | full\_and\_above | site | Confirmed 3 Oct 2026 |
| dm.images | inline | site | Confirmed 3 Oct 2026; caps as subforum.max\_images\_per\_post and subforum.max\_image\_mb defaults |
| dm.edit\_window\_minutes | 30 | site | Proposed, matching the sub-forum default |
| reports.max\_per\_member\_per\_day | 10 | site | Proposed |
| flags.rate\_limit\_refusals | 3 | site | Proposed; refusals within flags.rate\_limit\_window\_hours |
| flags.rate\_limit\_window\_hours | 24 | site | Proposed |
| flags.rapid\_deletions | 5 | site | Proposed; own-post deletions within flags.rapid\_deletion\_window\_minutes |
| flags.rapid\_deletion\_window\_minutes | 60 | site | Proposed |
| notifications.max\_emails\_per\_day | 1 | site | Confirmed 3 Oct 2026; optional kinds only |
| subforum.min\_read\_role | provisional | sub-forum | Guest Lobby sets guest |
| subforum.min\_thread\_role | provisional | sub-forum | Guest Lobby sets guest |
| subforum.min\_reply\_role | provisional | sub-forum | Guest Lobby sets guest |
| subforum.post\_rate\_limit | none | sub-forum | Serious Discussion 1 per 24h; Seminars 1 per 7d; rolling window |
| subforum.post\_rate\_limit\_by\_role | {} (no overrides) | sub-forum | Decided 3 Oct 2026; maps role to a limit that replaces post\_rate\_limit for that role; none set at launch |
| subforum.thread\_rate\_limit | none | sub-forum |  |
| subforum.images | off | sub-forum | Values off, inline, attachments |
| subforum.max\_images\_per\_post | 4 | sub-forum | Proposed |
| subforum.max\_image\_mb | 5 | sub-forum | Proposed; per image, before re-encoding |
| subforum.links | full\_and\_above | sub-forum | Decided 3 Oct 2026; values off, full\_and\_above, on; outside links only; Guest Lobby sets off |
| mentions.max\_notified\_per\_post | 10 | site | Proposed |
| pagination.posts\_per\_thread\_page | 20 | site | Confirmed |
| pagination.threads\_per\_subforum\_page | 30 | site | Confirmed |
| pagination.search\_results\_per\_page | 20 | site | Confirmed |
| pagination.profile\_posts\_per\_page | 20 | site | Confirmed |
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
8. Admin and Owner can read any thread including DMs; Moderators read a DM only under an unexpired DMAccessGrant covering a participant. Every DM read is audited, whoever reads it, and a search that shows DM text counts as a read.
9. Every request from an unauthenticated session is refused except login, password reset, invitation acceptance, Stripe webhooks, legal pages, and the web app manifest, its icons and the service worker script, which carry no member content. No TOTP, no session. Password reset mail goes only to verified addresses.
10. AuditEntry rows are written inside the same database transaction as the action they record, and the table rejects updates and deletes.
11. Posts are soft-deleted only. Erasure anonymises per the Privacy section and is the one path that clears personal fields.
12. A ban on a Guest or Provisional opens a SponsorReview in the same transaction, unless the sponsor is Admin or Owner. Nothing happens to the sponsor until an Admin or Owner decides the review.
13. Locked threads accept replies only from Admins, Owners and Moderators of that sub-forum; archived threads accept no changes from anyone, except Owner unarchiving and Admin redaction in the Graveyard (rule 26). A rejected post is shown to its author only in their post history, never in the thread. Moderator rank counts toward a sub-forum's minimum roles only where the member moderates.
14. The service worker caches only static shell assets, including one static offline page that contains no member content. It never caches server-rendered pages, HTMX fragments or attachments. The logout response sends `Clear-Site-Data: "cache", "storage"`.
15. An account with status invited reaches only its onboarding pages (TOTP enrolment, identity details, onboarding status, declining the invitation, logout). It never appears in member lists, search, mentions or the pedigree. The invitation acceptance page is the only onboarding page served without a session.
16. Sponsorship slots go first to the sponsor's active sponsorships, then to live invitations (pending, accepted, waitlisted) in the order they were sent. When a slot frees, the earliest-sent live invitation without one takes it, and a waitlisted invitation becomes accepted at that moment. Approval turns the invitation's slot into the new Sponsorship; every other ending frees it.
17. Only an Admin or Owner approves or declines an invitation, and only once its IdentityRecord is submitted. Approving a waitlisted invitation is an explicit over-cap approval and is audited as one. The sponsor may rescind, and the invitee may decline, at any point before approval.
18. A pending invitation expires invitation.expiry\_days after it was sent. Accepted and waitlisted invitations never expire.
19. When an invitation ends without approval, its invited account can no longer sign in, and after invitation.ended\_account\_deletion\_days the account and its IdentityRecord are deleted. This is the one routine hard delete outside erasure. The Invitation row and the audit entries survive it.
20. Only an Admin or Owner can grant a complimentary subscription, which moves a Guest to Provisional as payment would.
21. Post bodies are rendered once, server-side, through the Markdown allow-list; raw HTML is escaped, never rendered. Whether an outside link is clickable depends on the sub-forum's links setting and the author's role when the post is rendered.
22. No image is ever loaded from another site. Uploads are images of the allowed types only, re-encoded by the server before storage, within the sub-forum's per-post caps.
23. A mention notifies only a member who can read the thread, and only once the post is visible (released, if held). Invited accounts cannot be mentioned. No more than mentions.max\_notified\_per\_post notifications go out per post.
24. A quote may only reference a visible post in the same thread or DM. Editing, deleting or anonymising a quoted post re-renders every post that quotes it; a deleted original's text no longer appears in the quoting post, and staff read it in that post's source and history.
25. Authors edit and delete their own posts within the edit window and never in a locked thread. Staff edit and delete any post in sub-forums they moderate; deleting another member's post requires a reason; a staff edit is marked as one. Earlier post bodies and thread titles are kept as revisions.
26. Sending a thread to the Graveyard is open to Moderators in their sub-forums, Admins and Owners, with a reason; sending one to the Classics, and archiving in place, to Admins and Owners; unarchiving or bringing a thread back, to Owners only. Only Admins edit Graveyard thread titles. Moderators redact posts in sub-forums they moderate (including Graveyard threads from them), Admins and Owners anywhere; every redaction is audited, and only Owners purge earlier revisions. DM threads cannot be sent to either area. When an action widens a thread's audience, the confirmation says so.
27. Pinning is for Admins and Owners; locking and unlocking also for Moderators in their sub-forums. Moderators move threads only between sub-forums they moderate.
28. Profiles show no post count to anyone but the member themselves.
29. Moderators' search covers held and rejected posts in sub-forums they moderate. Admins and Owners search all DMs; a Moderator searches only the DMs of members covered by a grant they hold, while it lasts; nobody else's search includes DMs.
30. A DM can be started or added to only when every pair of participants may message each other under the Direct messages table, neither has blocked the other, the conversation stays within dm.max\_participants, and the starter is within dm.max\_new\_conversations\_per\_day. A member who may no longer message someone keeps reading the conversation but cannot send to it.
31. A DM participant reads messages from their joined\_at to their left\_at. Members cannot remove each other; only Admins and Owners remove a participant. DM messages are never held and never count toward promotion, held-post or rate-limit counts.
32. last\_read\_at is never shown to anyone but its owner. The visibility notice appears at the top of every conversation and under every compose box.
33. Reports of DM messages are visible only to Admins and Owners. The reported member never sees who reported them. A member's reports beyond reports.max\_per\_member\_per\_day are refused, and a repeat report of the same thing by the same member creates nothing.
34. Queue items are visible as the queue table says. Every queue action rechecks, inside its transaction, that the item is still open. Only Admins and Owners resolve escalated items, sponsor reviews and DM reports. Any queue item a Moderator can see can be escalated.
35. Hiding a post is a soft delete with a preset reason, and notifies the author.
36. A pending moderation action can be declined by an eligible approver or withdrawn by its initiator; neither is public. A Moderator's suspension or hold is limited to their sub-forums unless an Admin or Owner initiates or approves it; Probation and bans always need an Admin or Owner. Actions stop applying at ends\_at when permissions are checked, whatever the expiry job has done. A ban is in force while its action is active and changes neither account status nor roles, so lifting it restores everything.
37. Moderators' per-member view excludes DMs, blocks, payment, sessions and identity details. Revealing identity details is audited. Only Admins and Owners read the audit log, and no audit payload contains a real name or DM text.
38. Feed items involving a DM are visible only to Admins and Owners; Moderators see feed items about their sub-forums or about members.
39. Emails carry no post text, DM text or sender. Optional email kinds are off unless the member turns them on, and they are combined into at most notifications.max\_emails\_per\_day emails.
40. No price is a code constant: membership, extras and gifts use Stripe price ids from configuration, and the ban fee is computed from the registry. Membership is annual only, with no trial.
41. A Guest may pay, or accept a gift, at any time after approval; either moves them to Provisional at once. Only a sponsor can gift, and only to their own approved Guest invitee.
42. Granting a staff role creates a staff comp and revoking it ends the comp, after which lapse rules apply unless an Admin or Owner keeps the member comped. A comp with comped\_until ends then. Only an Owner extends a comp.
43. read\_only\_at is computed by the forum from the end of the paid period or comp plus billing.lapse\_grace\_days, never from Stripe's status; any successful payment restores the member at once. When the account goes read-only the forum cancels the Stripe subscription.
44. A read-only (lapsed) member reads only sub-forums with subforum.readable\_when\_lapsed; after billing.lapse\_restrict\_days their access narrows to the billing page, their own post history and DMs with their sponsor and staff, and their pre-Tenure sponsees enter sponsorship transfer. While lapsed they cannot invite or recommend, and the Provisional clock excludes lapsed days. Lapsing never removes an account.
45. Every ordinary ban is payable: the fee is billing.ban\_reversal\_fee\_cents multiplied by billing.ban\_reversal\_fee\_multiplier once for each earlier paid-off ban. A banned member reaches the ban payment page. Payment lifts the ban only.
46. Only an Owner imposes or annuls a Permanent Ban, and annulment needs a written reason. A Permanently Banned account cannot sign in. Every invitation is checked against the permanent-ban list, and a match is shown to the reviewing Admin, never acted on automatically. The list is visible only to Admins and Owners and survives erasure.
47. A deleted post leaves a placeholder naming who deleted it (the staff member, or "the author"). Rejected posts leave none.
48. Rap Sheet links to posts pass the reader's normal permission checks; deleted and hidden posts show as removed, redacted posts in their redacted form, and DM posts are never linked.
49. Extras are for Provisional and above, bought once, kept through a lapse, and revoked only by a moderation action. Staff may reset an avatar or caption to the default.
50. Each Stripe event is processed at most once, keyed by its event id, in the same transaction as its effects. Billing pages are visible only to the member; billing emails are account emails and always sent.

### First Claude Code session: milestone 1

Scope: repository scaffold, data model, permission service, seed data, tests. No member-facing UI beyond login and TOTP enrolment; Django admin is enough for the Owner to inspect data.

Definition of done:

- Docker Compose brings up Django and PostgreSQL; `manage.py seed` creates the seven roles, an Owner account, and the four initial sub-forums with the settings in this document.
- Migrations for every entity in the Data model table, with the audit-log append-only rule applied in a migration.
- Settings registry in code with the defaults above; SubForum.settings and SiteSetting validate against it.
- Permission service with tests covering: role rank versus sub-forum minimums, Provisional held posts, rolling-window rate limits for all three limited sub-forums, Admin-alone moderation actions, sponsor cap enforcement, sponsor review on ban with Admin exemption, sponsoring suspension, DM visibility with and without a grant.
- allauth configured for email/password with mandatory TOTP; an account without TOTP cannot reach any forum view.
- A short README describing how to run it and pointing back to this document.

### Milestone 2: onboarding

Scope: build step 2 as decided above. Sponsors send invitations and can rescind them; invitees accept, create their account, enrol TOTP, give their real name and wait; Admins and Owners review, approve (including over the cap) or decline; invitees can decline at any point before approval. Includes the expiry and ended-account deletion jobs, the complimentary Guest-to-Provisional path, the Provisional-to-Full and Full-to-Tenured promotion workflow, and in-app notifications to sponsors when their invitee accepts, declines, is approved or is declined. Payment-driven promotion waits for billing in step 5.

Definition of done:

- Migration adding the accepted, invitee\_declined and rescinded invitation statuses and the new fields on Invitation, IdentityRecord and Subscription.
- Every transition in the onboarding table is a service function that writes its AuditEntry in the same transaction, and each has tests, including refusal when the actor lacks the right (a Moderator approving, a sponsor rescinding after approval, an invitee declining after approval).
- Slot tests: send order holds across pending and waitlisted invitations; a freed slot goes to the earliest-sent invitation; a later invitee who accepts first is still waitlisted; approval over the cap is audited.
- Tests that an invited account reaches only its onboarding pages, and appears nowhere else.
- The expiry job expires only pending invitations, and the deletion job removes only accounts whose invitation ended without approval, after the set number of days, leaving the Invitation row and audit entries in place.
- The comp path moves a Guest to Provisional with a comped Subscription and a provisional RoleAssignment, and only an Admin or Owner can use it.
- Pages, mobile-first: send invitation (showing whether it will hold a slot or wait), acceptance, onboarding status, review queue showing waiting time, invitation list for sponsors.

### Build step 3: forum pages

Scope: the forum index, sub-forum pages, thread pages, the editor, profiles and search, built plain and mobile-first, plus the Graveyard and the Classics, the Markdown allow-list, links, image uploads, mentions and quotes as decided above.

Definition of done:

- Migrations for the new fields on SubForum, Thread and PostRevision, the new ThreadTitleRevision and PostQuote tables, and the rename of the links value members\_only to full\_and\_above in stored settings. Seed creates the Graveyard and the Classics.
- Renderer tests: each allowed element renders; tables and three-level headings do not; raw HTML appears as text; outside links follow the setting and the author's role and carry the rel attributes; image addresses from other sites become links.
- Upload tests: allowed types pass, SVG and other types are refused, metadata is gone after re-encoding, both caps are enforced.
- Mention and quote tests: no notification to a member who cannot read the thread; held posts notify on release; a quote from another thread is refused; editing and deleting a quoted post re-render the quoting post.
- Thread-ending tests for every actor and action in rule 26, including the audience warning and Owner purge.
- Pages show 20, 30, 20 and 20 items as the registry says, with no "show all".

### Build step 4: moderation and direct messages

Scope: DMs (starting, group membership, replies, editing and deleting, blocking, the visibility notice, reporting), the moderation queue with hide, escalate, decline and withdraw, reports and the three automatic flags, scoped suspensions and holds, Probation, expiry and ban lifting, the two-tier per-member view, the audit log view, audited DM reads and DM search, the Mod feedback feed, and notifications with their page, unread count and pointer-only emails.

Definition of done:

- Migrations: ThreadParticipant added\_by and left\_at; Block; the reshaped Report; ModerationAction scope\_subforums, declined\_by, decline\_reason, public\_summary\_draft and the declined and withdrawn statuses; the read\_only kind renamed probation, with a data migration; Notification emailed\_at; NotificationPreference. Registry rows for every new setting.
- DM tests for every row of the Direct messages table: each pairing allowed and refused, restricted and banned members, the group cap and pairwise rule, joining and leaving visibility, blocking (including that staff cannot be blocked), the new-conversation cap, and that DM messages count toward nothing.
- Queue tests: each item type is visible to exactly the staff the table names; a second action on a handled item changes nothing; escalated items resolve only for Admins and Owners; hiding requires a preset reason and notifies the author.
- Report tests: the daily limit, duplicate reports, reporter anonymity towards the reported member, and DM reports invisible to Moderators.
- Flag tests at each threshold, including that a request-rate trip makes the account read-only and queues a flag.
- Moderation tests: decline and withdraw stay off the public record; a Moderator's suspension applies only in their sub-forums; site-wide actions and Probation refuse a Moderator-only pair; an action past ends\_at stops applying before the expiry job runs; lifting a ban restores the previous status and shows as lifted without the means.
- Audit tests: an Admin's DM read, a DM-bearing search and an identity reveal each write one entry; no entry's payload contains a real name or DM text.
- Feed and notification tests: Moderators never see DM-related feed items; emails contain no content or sender; optional kinds are off by default and batched to one a day.

### Build step 5: billing

Scope: annual membership through Stripe Checkout and the Customer Portal, webhooks, the lapse clock and both lapse stages, cancellation and renewal, staff and founding comps, ban payments with the doubling fee, the Permanent Ban and the permanent-ban list, gifts, the paid-extras catalogue with the avatar and caption, deleted-post placeholders, Moderator redaction, the Rap Sheet page, and the billing emails.

Definition of done:

- Migrations: Subscription's new fields; LapsePeriod, StripeEvent, Gift, Extra, Entitlement and PermanentBanRecord; Charge kinds extra and gift; ModerationAction kind permanent\_ban and status annulled; User avatar and caption. Registry rows for every new setting. Seed adds the avatar\_caption extra and staff comps for existing staff.
- Webhook tests from recorded Stripe payloads, without calling Stripe: each handled event; the same event twice changes nothing; events out of order leave the right state; unhandled events are recorded and ignored.
- Lapse tests on a fixed clock: grace from period end regardless of Stripe status; read-only and cancellation at read\_only\_at; the restriction stage and sponsorship transfer at 90 days; payment at each stage restores everything; a lapsed Provisional's clock pauses; lapsed members cannot invite or recommend; subforum.readable\_when\_lapsed is honoured.
- Comp tests: granting and revoking a staff role starts and ends a staff comp; founding comps end on their date with the warning email; only an Owner extends.
- Ban tests: the fee is $10, $20, $40 across successive paid-off bans; payment lifts only the ban; a Permanent Ban cannot be paid, blocks sign-in, writes the list entry, flags a matching invitation to the reviewer, and can be annulled only by an Owner with a reason.
- Gift, extra, placeholder, redaction and Rap Sheet tests matching rules 41, 47, 48 and 49, and redaction by a Moderator outside their sub-forums refused.
- Email tests: each billing email is sent, as a pointer, at the right time, and Stripe's own dunning emails are assumed off.

Later milestones follow the build order in Recommended direction.

### Repository

Public (decided 3 Oct 2026), at [github.com/veekbee/something\_forum](https://github.com/veekbee/something_forum). Expected layout: this document at `docs/DESIGN.md` and session guidance at `CLAUDE.md` in the root. The value is the community, not the code, and a readable codebase is a security asset rather than a liability if the usual hygiene holds: no secrets in the repository (environment variables only, with a committed `.env.example`), secret scanning and dependency alerts switched on, a security policy file, and prompt patching of dependencies. One design consequence: the watermarking scheme becomes public, so its parameters (which characters, which positions) must live in configuration, not code, and the scheme should be treated as a tracing aid rather than a secret. Licence: MIT (decided 3 Oct 2026).

Change workflow (decided 3 Oct 2026): this file is the single authority for the design. Design discussions happen in Claude.ai chats, which read the repository but do not commit to it. They propose changes as a unified diff against this file (and against `CLAUDE.md` where session guidance changes), and the implementation session in Claude Code reviews the patch, applies it, makes any code changes it implies, and commits. A patch that conflicts with the current file, or with code already built, is sent back with the conflict described rather than merged by guesswork. The Claude.ai design doc that preceded this file is kept as history only.

## Open questions for later sessions

Decided on 2 and 3 Oct 2026 and written into the sections above: platform (Django), sponsorship caps and transfer, Provisional-period thresholds, identity-check depth, Moderator DM access, payment provider (Stripe), hosting and jurisdiction (US, GDPR as an ideal), initial sub-forums, authentication, repository visibility, Guest Lobby posting and rate limit, per-role rate limits, held-post counting, sponsor caps for scoped Moderators, sponsor review in place of the automatic sponsor ban, held-post visibility, staff notes without approval, staff acting only on lower ranks, invitation waitlist, replies in locked threads, archived threads, rejected-post visibility, password reset, code licence (MIT), platforms (browser only, installable to the home screen), onboarding flow and invitation states, account created at acceptance, invitee decline and sponsor rescind, email-only identity check at launch, complimentary Guest-to-Provisional path, invitation expiry, Markdown allow-list, outside links, images, mentions, quotes, page sizes, post and thread editing and deletion, Thread Graveyard and Thread Classics, profiles without post counts, plain pages with the look decided later, deleted-quote display, direct messages (who may message whom, groups, content, blocking, read position, notice, reporting), the moderation queue, hiding, escalation, reports, automatic flags, declining and withdrawing actions, scoped suspensions and holds, Probation as the disciplinary status, public summaries, expiry and ban lifting, staff views, audited DM reads and DM search, the Mod feedback feed, notifications and email, blocks visible to Admins and Owners only, escalation of any queue item, limited actions covering several sub-forums, following threads, DM participant removal by Admins and Owners, membership price and plan, no trial, paying after approval, staff and founding comps, the lapse clock, cancellation, lapsed members' duties, lapse stages, renewal, payable bans with a doubling fee, the Permanent Ban and its list, the Rap Sheet, deleted-post placeholders, Moderator redaction, paid extras, gifts, discounts, Stripe's emails, billing emails.

Still open:

- [ ] Sponsorship cap for Full members (default 1 is assumed).
- [ ] Whether a member may voluntarily change sponsor, outside the forced transfer when a sponsor leaves.
- [ ] What fails the Provisional period, beyond a ban: for example a warning count or a Moderator recommendation.
- [ ] Proposed defaults awaiting confirmation: transfer grace period 30 days, edit window 30 minutes, audit retention 2 years, scraping threshold 600 requests per 10 minutes, deletion of ended invited accounts after 30 days.
- [ ] Whether someone whose invitation was declined by an Admin can be invited again, and by whom.
- [ ] The forum's visual look, in a design pass once the plain pages exist.
- [ ] Non-image attachments (for example PDFs for Seminars), if a need appears.
- [ ] Proposed caps awaiting confirmation: 4 images and 5 MB per image per post, 10 notified mentions per post, 8 DM participants, 10 new DM conversations and 10 reports per member per day, and the automatic flag thresholds.
- [ ] How a member who cannot sign in at all sends an appeal to Admins: a public form adds an unauthenticated page; an email address does not. Banned members can sign in and appeal by DM to Admins and Owners (decided 3 Oct 2026).
- [ ] Deleting audit entries at the end of the retention period, given the append-only trigger (deferred 3 Oct 2026).
- [ ] Web push notifications: whether to offer them, for which events, and what a notification may contain (a push message passes through Apple's or Google's servers, so no post content).
- [ ] Legal review of the member agreement and privacy notice before any member joins, including the automatic-renewal notice and the permanent-ban list's survival of erasure.
- [ ] Proposed: 90 days of read-only (lapsed) before access narrows.

## Sources

Comparison pages opened for the tooling section, as of October 2026: [Discourse vs Flarum vs NodeBB (elest.io)](https://blog.elest.io/discourse-vs-flarum-vs-nodebb-which-self-hosted-forum-platform-in-2026/), [Self-hosted forum software compared (SSD Nodes)](https://www.ssdnodes.com/learn/self-hosted-forum-software), [Best forum software in 2026 (xenforomobile.app)](https://xenforomobile.app/what-is-the-best-forum-software-in-2026-an-honest-comparison/). Platform feature claims in the table are from working knowledge of each product and were not independently verified; they matter only if the build stalls and the XenForo fallback is revisited.
