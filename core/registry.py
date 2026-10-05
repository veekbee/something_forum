"""Settings registry: every tunable value in the forum, with its default and where it may be set.

Site-wide values are stored in core.SiteSetting; sub-forum values in SubForum.settings. Both are
validated against this registry, so a new setting is one entry here. Defaults are from the
Implementation brief in docs/DESIGN.md; nothing here is meant to be used as a code constant.
"""

import copy
from dataclasses import dataclass
from typing import Any, Callable

from django.core.exceptions import ValidationError

SITE = "site"
SUBFORUM = "subforum"

ROLE_NAMES = ("guest", "provisional", "full", "tenured", "moderator", "admin", "owner")


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError("must be a positive integer")


def _non_negative_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError("must be zero or a positive integer")


def _optional(validator):
    """None means unlimited (or off, depending on the setting)."""

    def validate(value):
        if value is not None:
            validator(value)

    return validate


def _hour(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
        raise ValidationError("must be an hour from 0 to 23")


def _bool(value):
    if not isinstance(value, bool):
        raise ValidationError("must be true or false")


def _choice(*choices):
    def validate(value):
        if value not in choices:
            raise ValidationError(f"must be one of {', '.join(choices)}")

    return validate


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("must be some text")


def _role(value):
    _choice(*ROLE_NAMES)(value)


def _rate_limit(value):
    """A rolling-window limit: {"count": N, "window_hours": H}."""
    if not isinstance(value, dict) or set(value) != {"count", "window_hours"}:
        raise ValidationError('must be {"count": N, "window_hours": H} or null')
    _positive_int(value["count"])
    _positive_int(value["window_hours"])


def _rate_limit_by_role(value):
    """Maps a role name to a rate limit, or to null for unlimited."""
    if not isinstance(value, dict):
        raise ValidationError("must be an object mapping role names to rate limits")
    for role, limit in value.items():
        _role(role)
        _optional(_rate_limit)(limit)


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    scopes: frozenset
    validate: Callable[[Any], None]
    status: str
    description: str


_SETTINGS = [
    Setting("sponsorship.cap.full", 1, frozenset({SITE}), _optional(_non_negative_int), "assumed",
            "Active sponsorships a Full member may hold."),
    Setting("sponsorship.cap.tenured", 3, frozenset({SITE}), _optional(_non_negative_int), "confirmed",
            "Active sponsorships a Tenured member may hold."),
    Setting("sponsorship.cap.moderator", 7, frozenset({SITE}), _optional(_non_negative_int), "confirmed",
            "Active sponsorships a global Moderator may hold."),
    Setting("sponsorship.cap.admin", None, frozenset({SITE}), _optional(_non_negative_int), "confirmed",
            "Active sponsorships an Admin may hold; null is unlimited."),
    Setting("sponsorship.cap.owner", None, frozenset({SITE}), _optional(_non_negative_int), "confirmed",
            "Active sponsorships an Owner may hold; null is unlimited."),
    Setting("sponsorship.transfer_grace_days", 30, frozenset({SITE}), _positive_int, "proposed",
            "Days before an unfinished sponsorship transfer goes to an Admin or Owner."),
    Setting("sponsorship.max_open_vouch_requests", 3, frozenset({SITE}), _positive_int, "proposed",
            "Requests to vouch a waiting sponsee may have open at once."),
    Setting("sponsorship.review_on_member_ban", True, frozenset({SITE}), _bool, "confirmed",
            "Open a sponsor review when a Guest or Provisional is banned."),
    Setting("promotion.full.min_days", 90, frozenset({SITE}), _positive_int, "confirmed",
            "Days as Provisional before eligibility for Full."),
    Setting("promotion.full.min_posts", 25, frozenset({SITE}), _positive_int, "confirmed",
            "Posts as Provisional before eligibility for Full."),
    Setting("promotion.tenured.min_days", 90, frozenset({SITE}), _positive_int, "confirmed",
            "Days as Full before eligibility for Tenured."),
    Setting("provisional.held_posts", 5, frozenset({SITE, SUBFORUM}), _non_negative_int, "confirmed",
            "A Guest's or Provisional's first N posts site-wide are held for review."),
    Setting("billing.lapse_grace_days", 14, frozenset({SITE}), _positive_int, "confirmed",
            "Days after the paid period ends before the account becomes read-only."),
    Setting("invitation.expiry_days", 14, frozenset({SITE}), _positive_int, "confirmed",
            "Days before an invitation nobody has accepted expires."),
    Setting("invitation.ended_account_deletion_days", 30, frozenset({SITE}), _positive_int, "proposed",
            "Days before an invited account whose invitation ended without approval is deleted."),
    Setting("billing.ban_reversal_fee_cents", 1000, frozenset({SITE}), _positive_int, "confirmed",
            "Fee for lifting a member's first paid-off ban, in cents."),
    Setting("billing.ban_reversal_fee_multiplier", 2, frozenset({SITE}), _positive_int, "confirmed",
            "The ban fee is multiplied by this once for each ban the member has already paid off."),
    Setting("billing.lapse_restrict_days", 90, frozenset({SITE}), _positive_int, "proposed",
            "Days of read-only (lapsed) before access narrows."),
    Setting("billing.renewal_reminder_days", 30, frozenset({SITE}), _positive_int, "confirmed",
            "Days before an annual renewal that the reminder email goes out."),
    Setting("billing.read_only_warning_days", 3, frozenset({SITE}), _positive_int, "confirmed",
            "Days before going read-only that the warning email goes out."),
    Setting("billing.founding_comp_months", 12, frozenset({SITE}), _positive_int, "confirmed",
            "Months after billing launches that founding comps end."),
    Setting("billing.founding_comp_warning_days", 30, frozenset({SITE}), _positive_int, "confirmed",
            "Days before a founding comp ends that the member is emailed."),
    Setting("extras.caption_max_chars", 40, frozenset({SITE}), _positive_int, "confirmed",
            "Longest caption the avatar and caption extra allows."),
    Setting("auth.require_totp", True, frozenset({SITE}), _bool, "confirmed",
            "Every account must enrol TOTP before reaching the forum."),
    Setting("retention.audit_years_after_departure", 2, frozenset({SITE}), _positive_int, "proposed",
            "Years audit and moderation records are kept after a member leaves."),
    Setting("export.link_hours", 24, frozenset({SITE}), _positive_int, "proposed",
            "Hours a data export's download link works."),
    Setting("export.keep_days", 7, frozenset({SITE}), _positive_int, "proposed",
            "Days a built data export is kept before its file is deleted."),
    Setting("export.min_days_between", 7, frozenset({SITE}), _positive_int, "proposed",
            "Days a member waits between data exports."),
    Setting("erasure.deadline_days", 30, frozenset({SITE}), _positive_int, "confirmed",
            "Days an Owner has to run an erasure request."),
    Setting("erasure.deferral_days", 30, frozenset({SITE}), _positive_int, "confirmed",
            "Days one deferral of an erasure request adds."),
    Setting("jobs.daily_hour_utc", 3, frozenset({SITE}), _hour, "proposed",
            "Hour (UTC) the daily jobs run."),
    Setting("scraping.requests_per_10_min", 600, frozenset({SITE}), _positive_int, "proposed",
            "Per-member page and fragment requests per 10 minutes before the account is made read-only."),
    Setting("session.concurrency_window_minutes", 30, frozenset({SITE}), _positive_int, "proposed",
            "Two devices active within this many minutes in different countries count as concurrent."),
    Setting("session.member_lifetime_days", 30, frozenset({SITE}), _positive_int, "proposed",
            "Longest a member's session lasts."),
    Setting("session.member_idle_days", 14, frozenset({SITE}), _positive_int, "proposed",
            "A member's session ends after this many days unused."),
    Setting("session.staff_lifetime_days", 7, frozenset({SITE}), _positive_int, "proposed",
            "Longest an Admin's or Owner's session lasts."),
    Setting("session.staff_idle_days", 1, frozenset({SITE}), _positive_int, "proposed",
            "An Admin's or Owner's session ends after this many days unused."),
    Setting("session.retention_days", 90, frozenset({SITE}), _positive_int, "proposed",
            "Days a session record is kept after the session ends."),
    Setting("watermark.enabled", True, frozenset({SITE}), _bool, "confirmed",
            "Mark post bodies and DM messages per session. Characters and positions are configuration."),
    Setting("emoji.max_height_px", 128, frozenset({SITE}), _positive_int, "proposed",
            "Tallest custom emoji upload, in pixels."),
    Setting("emoji.max_aspect_ratio", 3, frozenset({SITE}), _positive_int, "confirmed",
            "A custom emoji may be up to this many times as wide as it is tall."),
    Setting("emoji.max_kb", 512, frozenset({SITE}), _positive_int, "proposed",
            "Largest custom emoji upload, in kilobytes."),
    Setting("emoji.allow_animated", True, frozenset({SITE}), _bool, "confirmed",
            "Whether animated GIF emoji are accepted."),
    Setting("legal.reviewed", False, frozenset({SITE}), _bool, "confirmed",
            "Set by an Owner once the legal text is reviewed. Until then no invitation can be sent."),
    Setting("reading.unread_window_days", 30, frozenset({SITE}), _positive_int, "proposed",
            "A thread never opened counts as unread only if its last post is this recent."),
    Setting("reading.prune_after_days", 365, frozenset({SITE}), _positive_int, "proposed",
            "Read positions on threads untouched this long are deleted."),
    Setting("site.name", "Something Forum", frozenset({SITE}), _text, "confirmed",
            "The forum's name: page titles, the home-screen manifest and authenticator apps. A working title."),
    Setting("dm.max_participants", 8, frozenset({SITE}), _positive_int, "proposed",
            "People in one direct-message conversation."),
    Setting("dm.max_new_conversations_per_day", 10, frozenset({SITE}), _positive_int, "proposed",
            "Conversations one member may start in 24 hours."),
    Setting("dm.links", "full_and_above", frozenset({SITE}), _choice("off", "full_and_above", "on"), "confirmed",
            "Outside links in direct messages."),
    Setting("dm.images", "inline", frozenset({SITE}), _choice("off", "inline", "attachments"), "confirmed",
            "Uploaded images in direct messages; caps as the sub-forum defaults."),
    Setting("dm.edit_window_minutes", 1440, frozenset({SITE}), _optional(_positive_int), "confirmed",
            "Minutes after sending that a message may be edited or deleted."),
    Setting("reports.max_per_member_per_day", 10, frozenset({SITE}), _positive_int, "proposed",
            "Reports one member may make in 24 hours."),
    Setting("flags.rate_limit_refusals", 3, frozenset({SITE}), _positive_int, "proposed",
            "Posts refused by rate limits within the window that raise a flag."),
    Setting("flags.rate_limit_window_hours", 24, frozenset({SITE}), _positive_int, "proposed",
            "Window for counting rate-limit refusals."),
    Setting("flags.rapid_deletions", 5, frozenset({SITE}), _positive_int, "proposed",
            "Own-post deletions within the window that raise a flag."),
    Setting("flags.rapid_deletion_window_minutes", 60, frozenset({SITE}), _positive_int, "proposed",
            "Window for counting own-post deletions."),
    Setting("notifications.max_emails_per_day", 1, frozenset({SITE}), _positive_int, "confirmed",
            "Emails for optional notification kinds, combined, per member per day."),
    Setting("subforum.min_read_role", "provisional", frozenset({SUBFORUM}), _role, "confirmed",
            "Lowest role that may read."),
    Setting("subforum.min_thread_role", "provisional", frozenset({SUBFORUM}), _role, "confirmed",
            "Lowest role that may start a thread."),
    Setting("subforum.min_reply_role", "provisional", frozenset({SUBFORUM}), _role, "confirmed",
            "Lowest role that may reply."),
    Setting("subforum.post_rate_limit", None, frozenset({SUBFORUM}), _optional(_rate_limit), "confirmed",
            "Posts per member over a rolling window; thread starts count. Null is unlimited."),
    Setting("subforum.post_rate_limit_by_role", {}, frozenset({SUBFORUM}), _rate_limit_by_role, "confirmed",
            "Per-role replacements for post_rate_limit."),
    Setting("subforum.thread_rate_limit", None, frozenset({SUBFORUM}), _optional(_rate_limit), "confirmed",
            "New threads per member over a rolling window. Null is unlimited."),
    Setting("subforum.images", "off", frozenset({SUBFORUM}), _choice("off", "inline", "attachments"), "confirmed",
            "Uploaded images: none, placed within the post, or shown beneath it."),
    Setting("subforum.max_images_per_post", 4, frozenset({SUBFORUM}), _positive_int, "proposed",
            "Images allowed in one post."),
    Setting("subforum.max_image_mb", 5, frozenset({SUBFORUM}), _positive_int, "proposed",
            "Largest image upload, in megabytes, before re-encoding."),
    Setting("subforum.links", "full_and_above", frozenset({SUBFORUM}), _choice("off", "full_and_above", "on"),
            "confirmed", "Whether outside links are clickable: never, from Full members and above, or always."),
    Setting("mentions.max_notified_per_post", 10, frozenset({SITE}), _positive_int, "proposed",
            "Mentions in one post that send notifications."),
    Setting("pagination.posts_per_thread_page", 20, frozenset({SITE}), _positive_int, "confirmed",
            "Posts per page of a thread."),
    Setting("pagination.threads_per_subforum_page", 30, frozenset({SITE}), _positive_int, "confirmed",
            "Threads per page of a sub-forum."),
    Setting("pagination.search_results_per_page", 20, frozenset({SITE}), _positive_int, "confirmed",
            "Search results per page."),
    Setting("pagination.profile_posts_per_page", 20, frozenset({SITE}), _positive_int, "confirmed",
            "Posts per page of a member's post history."),
    Setting("subforum.edit_window_minutes", 1440, frozenset({SUBFORUM}), _optional(_positive_int), "confirmed",
            "Minutes after posting that the author may edit or delete, and the starter edit the title. Null is unlimited."),
    Setting("subforum.readable_when_lapsed", True, frozenset({SUBFORUM}), _bool, "confirmed",
            "Whether read-only (lapsed) members can read this sub-forum."),
    Setting("subforum.hold_posts", "first_n_provisional", frozenset({SUBFORUM}),
            _choice("off", "first_n_provisional", "all"), "confirmed",
            "Which posts are held for review."),
]

REGISTRY = {s.key: s for s in _SETTINGS}


def get(key):
    try:
        return REGISTRY[key]
    except KeyError:
        raise ValidationError(f"unknown setting {key!r}") from None


def validate(key, value, scope):
    setting = get(key)
    if scope not in setting.scopes:
        raise ValidationError(f"{key} cannot be set at {scope} scope")
    try:
        setting.validate(value)
    except ValidationError as e:
        raise ValidationError(f"{key}: {e.messages[0]}") from None


def validate_subforum_settings(settings):
    if not isinstance(settings, dict):
        raise ValidationError("sub-forum settings must be an object")
    for key, value in settings.items():
        validate(key, value, SUBFORUM)


def site_value(key):
    """The site-wide value: SiteSetting if the Owner set one, else the registry default."""
    from core.models import SiteSetting

    setting = get(key)
    row = SiteSetting.objects.filter(key=key).only("value").first()
    return row.value if row is not None else copy.deepcopy(setting.default)


def subforum_value(subforum, key):
    """A sub-forum's value: its own override, then the site value if the key has site scope,
    then the registry default."""
    setting = get(key)
    if key in subforum.settings:
        return subforum.settings[key]
    if SITE in setting.scopes:
        return site_value(key)
    return copy.deepcopy(setting.default)
