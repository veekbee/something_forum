from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.fields import EncryptedTextField
from core.immutability import HistoryRowMixin


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **fields):
        user = self.model(email=self.normalize_email(email), **fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, *args, **kwargs):
        raise NotImplementedError("Use `manage.py seed` to create the Owner account.")


class User(AbstractBaseUser):
    """Login identity and public profile only. Trust level comes from RoleAssignment rows;
    identity-check data lives in IdentityRecord."""

    class Status(models.TextChoices):
        INVITED = "invited"
        GUEST = "guest"
        ACTIVE = "active"
        READ_ONLY = "read_only"
        SUSPENDED = "suspended"
        BANNED = "banned"
        REMOVED = "removed"
        TOMBSTONE = "tombstone"

    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INVITED)
    joined_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    # Removal ends a membership; it is not discipline (rule 66). removed_by is empty when the member
    # left on their own. An invited account whose invitation ended is also status removed, but has no
    # removed_at: it was never a membership.
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    removal_reason = models.TextField(blank=True)
    # The avatar and caption extra (rule 49); without it a member has a generated avatar.
    avatar = models.ForeignKey("boards.Attachment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    caption = models.CharField(max_length=40, blank=True)

    class EmojiDisplay(models.TextChoices):
        IMAGES = "images"
        STILL = "still"
        NAMES = "names"

    # How custom emoji show to this member (rule 62): images, still images, or names only.
    emoji_display = models.CharField(max_length=8, choices=EmojiDisplay.choices, default=EmojiDisplay.IMAGES)

    class ColourScheme(models.TextChoices):
        DEVICE = "device"
        LIGHT = "light"
        DARK = "dark"

    # Light or dark pages: the device's choice unless the member picks one (rule 76).
    colour_scheme = models.CharField(max_length=8, choices=ColourScheme.choices, default=ColourScheme.DEVICE)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.display_name or self.email

    @property
    def is_active(self):
        # Removed and tombstoned accounts cannot sign in. Banned members can, so that they can
        # pay the reversal fee; the permission service refuses them everything else.
        return self.status not in (self.Status.REMOVED, self.Status.TOMBSTONE)

    # Django's permission flags are unused: authorisation goes through core.permissions.can().
    # These keep third-party code that asks Django directly on the deny side.
    def has_perm(self, perm, obj=None):
        return False

    def has_module_perms(self, app_label):
        return False


class Role(models.Model):
    name = models.CharField(max_length=32, unique=True)
    rank = models.PositiveSmallIntegerField(unique=True)
    is_staff = models.BooleanField(default=False)

    class Meta:
        ordering = ["-rank"]

    def __str__(self):
        return self.name


class RoleAssignment(HistoryRowMixin, models.Model):
    """A role held by a member. Trust level is the highest unrevoked assignment with no
    scope_subforum; a Moderator assignment with a scope limits moderation to that sub-forum."""

    closing_fields = ("revoked_at", "revoked_by")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="role_assignments")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="+")
    scope_subforum = models.ForeignKey(
        "boards.SubForum", null=True, blank=True, on_delete=models.PROTECT, related_name="moderator_assignments"
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reason = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["user"], condition=Q(revoked_at__isnull=True), name="roleassign_active")]

    def __str__(self):
        scope = f" in {self.scope_subforum}" if self.scope_subforum_id else ""
        return f"{self.user} {self.role}{scope}"


class Block(models.Model):
    """One member blocking another from DMs and mention notifications (docs/DESIGN.md, Direct
    messages). The blocked member is not told. Staff cannot be blocked."""

    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="blocks_made")
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="blocks_received")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked"], name="one_block_per_pair"),
            models.CheckConstraint(condition=~Q(blocker=models.F("blocked")), name="no_self_block"),
        ]


class IdentityRecord(models.Model):
    """The identity check. Kept apart from the profile, encrypted, and readable only through
    its own permission check (identity.read)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="identity")
    real_name = EncryptedTextField(blank=True)
    phone = EncryptedTextField(blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    vouching_notes = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reduced_at = models.DateTimeField(null=True, blank=True)


class UserSession(models.Model):
    """One signed-in session (docs/DESIGN.md, Session binding; rules 57 to 59). Session binding and
    the per-session watermark key off this row. Deleted session.retention_days after it ends, so
    nothing may block its deletion: flags and audit entries refer to it by id only."""

    class RevokeReason(models.TextChoices):
        SIGNED_OUT = "signed_out"
        SIGNED_OUT_BY_MEMBER = "signed_out_by_member"
        CONCURRENT_LOCATION = "concurrent_location"
        FACTOR_RESET = "factor_reset"
        EXPIRED = "expired"
        IDLE = "idle"

    # CASCADE: deleting an account (rule 19) takes its session records with it.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessions")
    session_key = models.CharField(max_length=40, unique=True)
    # The random identifier from the long-lived device cookie. No client-side fingerprinting.
    device_id = models.CharField(max_length=64, blank=True)
    browser_family = models.CharField(max_length=40, blank=True)
    os_family = models.CharField(max_length=40, blank=True)
    # /24 for IPv4, /48 for IPv6; never the full address.
    ip_prefix = models.CharField(max_length=64, blank=True)
    # ISO country code from the local database at sign-in; empty when unknown.
    country = models.CharField(max_length=2, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=24, choices=RevokeReason.choices, blank=True)
    watermark_seed = models.CharField(max_length=64, db_index=True)

    @property
    def ended_at(self):
        return self.revoked_at
