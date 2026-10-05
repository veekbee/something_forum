"""Create the seven roles, the Owner account and the four initial sub-forums. Idempotent: it only
creates what is missing and never overwrites settings or passwords someone has since changed."""

import os

from allauth.account.models import EmailAddress
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from accounts.models import Role, RoleAssignment, User, check_display_name
from boards.models import SubForum

ROLES = [
    # name, rank, is_staff
    ("owner", 70, True),
    ("admin", 60, True),
    ("moderator", 50, True),
    ("tenured", 40, False),
    ("full", 30, False),
    ("provisional", 20, False),
    ("guest", 10, False),
]

SUBFORUMS = [
    {
        "slug": "guest-lobby",
        "name": "Guest Lobby and Introductions",
        "description": "Guests introduce themselves and meet members before paying.",
        "settings": {
            "subforum.min_read_role": "guest",
            "subforum.min_thread_role": "guest",
            "subforum.min_reply_role": "guest",
            "subforum.links": "off",
        },
    },
    {
        "slug": "general-discussion",
        "name": "General Discussion",
        "description": "Everyday conversation.",
        "settings": {},
    },
    {
        "slug": "serious-discussion",
        "name": "Serious Discussion",
        "description": "Considered replies; one post per member per day.",
        "settings": {"subforum.post_rate_limit": {"count": 1, "window_hours": 24}},
    },
    {
        "slug": "seminars",
        "name": "Seminars",
        "description": "Long-form, essay-length contributions; one post per member per week.",
        "settings": {"subforum.post_rate_limit": {"count": 1, "window_hours": 24 * 7}},
    },
]


# Read-only areas where threads end, readable by Provisional and above whatever a thread's origin.
ENDING_AREAS = [
    {
        "kind": "graveyard",
        "slug": "thread-graveyard",
        "name": "Thread Graveyard",
        "description": "Threads that were deleted. Kept for good, read-only.",
    },
    {
        "kind": "classics",
        "slug": "thread-classics",
        "name": "Thread Classics",
        "description": "Threads of especially high quality that have reached their end.",
    },
]


class Command(BaseCommand):
    help = "Create roles, the Owner account (from OWNER_* environment variables) and the initial sub-forums."

    @transaction.atomic
    def handle(self, *args, **options):
        for name, rank, is_staff in ROLES:
            Role.objects.update_or_create(name=name, defaults={"rank": rank, "is_staff": is_staff})
        self.stdout.write(f"Roles: {Role.objects.count()}")

        for position, spec in enumerate(SUBFORUMS, start=1):
            _, created = SubForum.objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "description": spec["description"],
                    "position": position,
                    "settings": spec["settings"],
                },
            )
            self.stdout.write(f"Sub-forum {spec['name']}: {'created' if created else 'exists'}")

        for position, spec in enumerate(ENDING_AREAS, start=len(SUBFORUMS) + 1):
            _, created = SubForum.objects.get_or_create(
                kind=spec["kind"],
                defaults={**spec, "position": position, "settings": {"subforum.min_read_role": "provisional"}},
            )
            self.stdout.write(f"{spec['name']}: {'created' if created else 'exists'}")

        self._seed_owner()
        self._seed_billing()

    def _seed_billing(self):
        """The avatar and caption extra, and staff comps for anyone already on staff."""
        from billing import lapse
        from billing.models import Extra, Subscription

        Extra.objects.get_or_create(
            key="avatar_caption",
            defaults={"name": "Custom avatar and caption", "stripe_price_setting": "STRIPE_PRICE_AVATAR_CAPTION",
                      "min_role": "provisional"},
        )
        Extra.objects.get_or_create(
            key="custom_emoji",
            defaults={"name": "Custom emoji", "stripe_price_setting": "STRIPE_PRICE_CUSTOM_EMOJI",
                      "min_role": "provisional"},
        )
        staff = User.objects.filter(role_assignments__revoked_at__isnull=True,
                                    role_assignments__role__name__in=lapse.STAFF_ROLES).distinct()
        for user in staff:
            if not Subscription.objects.filter(user=user, comp_reason=Subscription.CompReason.STAFF).exists():
                lapse.start_staff_comp(user)

    def _seed_owner(self):
        owner_role = Role.objects.get(name="owner")
        if RoleAssignment.objects.filter(role=owner_role, revoked_at__isnull=True).exists():
            self.stdout.write("Owner: exists")
            return

        email = os.environ.get("OWNER_EMAIL", "").strip()
        password = os.environ.get("OWNER_PASSWORD", "")
        display_name = os.environ.get("OWNER_DISPLAY_NAME", "Owner").strip() or "Owner"
        try:
            check_display_name(display_name)
        except ValidationError as exc:
            raise CommandError(f"OWNER_DISPLAY_NAME: {exc.messages[0]}") from exc
        if not email or not password:
            raise CommandError("No Owner yet: set OWNER_EMAIL and OWNER_PASSWORD to create one.")

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User.objects.create_user(
                email=email, password=password, display_name=display_name,
                slug=slugify(display_name) or "owner", status=User.Status.ACTIVE,
            )
        EmailAddress.objects.update_or_create(
            user=user, email=user.email, defaults={"verified": True, "primary": True}
        )
        RoleAssignment.objects.create(user=user, role=owner_role, reason="Founder (seed)")
        self.stdout.write(f"Owner: created {email}. Sign in and enrol TOTP before anything else.")
