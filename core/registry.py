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


def _bool(value):
    if not isinstance(value, bool):
        raise ValidationError("must be true or false")


def _choice(*choices):
    def validate(value):
        if value not in choices:
            raise ValidationError(f"must be one of {', '.join(choices)}")

    return validate


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
            "Days an invitee has to find a new sponsor before further action."),
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
    Setting("billing.ban_reversal_fee_cents", 1000, frozenset({SITE}), _positive_int, "confirmed",
            "Flat fee to reverse a ban, in cents."),
    Setting("auth.require_totp", True, frozenset({SITE}), _bool, "confirmed",
            "Every account must enrol TOTP before reaching the forum."),
    Setting("retention.audit_years_after_departure", 2, frozenset({SITE}), _positive_int, "proposed",
            "Years audit and moderation records are kept after a member leaves."),
    Setting("scraping.requests_per_10_min", 600, frozenset({SITE}), _positive_int, "proposed",
            "Per-member request limit before the account is made read-only."),
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
            "Whether posts may carry images."),
    Setting("subforum.links", "members_only", frozenset({SUBFORUM}), _choice("off", "members_only", "on"),
            "confirmed", "Whether posts may carry links."),
    Setting("subforum.edit_window_minutes", 30, frozenset({SUBFORUM}), _optional(_positive_int), "proposed",
            "Minutes after posting that the author may edit. Null is unlimited."),
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
