"""Django settings. All configuration comes from environment variables; see .env.example."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
if env.bool("DJANGO_READ_DOT_ENV", default=True):
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "core.admin_apps.ForumAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "core",
    "audit",
    "accounts",
    "sponsorship",
    "boards",
    "moderation",
    "billing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.NoIndexMiddleware",
    "core.middleware.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "core.middleware.SessionBindingMiddleware",
    "core.middleware.WatermarkStripMiddleware",
    "core.middleware.AccessControlMiddleware",
    "moderation.flags.RequestRateMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
                "core.context_processors.nav",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
# A cache shared by every server process: request-rate counting (design: anti-scraping) and
# allauth's TOTP replay protection both need one. `manage.py createcachetable` creates the table.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "forum_cache"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["allauth.account.auth_backends.AuthenticationBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "/"

# django-allauth: email and password only, no self-signup, verified email, mandatory TOTP
# (enforced by core.middleware.AccessControlMiddleware).
ACCOUNT_ADAPTER = "accounts.adapters.AccountAdapter"
ACCOUNT_FORMS = {"reset_password": "accounts.forms.ResetPasswordForm"}
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_ADAPTER = "accounts.adapters.MFAAdapter"  # issuer from the site.name setting

# Paths reachable without a session (design rule 9). Prefix match.
# Origins besides the site itself that images may load from: the object store's address when
# attachments are served from a bucket by signed URL. Space-separated.
CSP_EXTRA_IMG_SRC = env("CSP_EXTRA_IMG_SRC", default="").split()

# Session binding (rule 57): the country database on this server, in MaxMind DB format (GeoLite2
# Country or DB-IP Lite Country, under their own licence terms). Without one, countries are unknown
# and the concurrent-location check never fires.
GEOIP_COUNTRY_DATABASE = env("GEOIP_COUNTRY_DATABASE", default="")
# The long-lived cookie holding a random device identifier.
DEVICE_COOKIE_NAME = "device"
DEVICE_COOKIE_AGE = 2 * 365 * 24 * 3600

# Watermarking (rule 61). Which characters and where are configuration, not code. Two different
# zero-width characters with no other job, as hex code points: the first stands for 0, the second
# for 1. Never 200C or 200D. A mark goes after the first word of a body and then every N words.
WATERMARK_CHARS = "".join(chr(int(c, 16)) for c in env("WATERMARK_CODEPOINTS", default="200B,2060").split(","))
WATERMARK_EVERY_WORDS = env.int("WATERMARK_EVERY_WORDS", default=12)

PUBLIC_PATH_PREFIXES = [
    "/accounts/login/",
    "/accounts/2fa/authenticate/",  # second step of login, before the session is complete
    "/accounts/password/reset/",  # reset by link to a verified address; TOTP still applies at login
    "/robots.txt",
    "/legal/",
    # Installability (rule 14): no member content in any of these.
    "/manifest.webmanifest",
    "/sw.js",
    "/icons/",
    "/offline/",
    "/invitations/accept/",
    "/billing/stripe/webhook/",
]
# The only pages an account with status invited may reach, besides TOTP enrolment (design rule 15).
ONBOARDING_PATH_PREFIXES = ["/onboarding/"]
# Paths a signed-in member without TOTP may reach, so they can enrol.
TOTP_ENROLMENT_PATH_PREFIXES = [
    "/accounts/2fa/",
    "/accounts/reauthenticate/",
    "/accounts/logout/",
]

# Encrypted identity fields (accounts.fields). Comma-separated Fernet keys, newest first.
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEY", default=[])

EMAIL_CONFIG = env.email("EMAIL_URL", default="consolemail://")
vars().update(EMAIL_CONFIG)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="forum@example.com")
# The forum's own address, for links in pointer emails.
SITE_URL = env("SITE_URL", default="http://localhost:8000")

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Object storage: private bucket, every URL signed and short-lived. Without a bucket
# (local development) files go to local disk, which no URL route serves.
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")
if AWS_STORAGE_BUCKET_NAME:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "endpoint_url": env("AWS_S3_ENDPOINT_URL", default=None),
                "region_name": env("AWS_S3_REGION_NAME", default=None),
                "default_acl": "private",
                "querystring_auth": True,
                "querystring_expire": 300,
                "file_overwrite": False,
            },
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
else:
    MEDIA_ROOT = BASE_DIR / "media"

STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")
# Prices live in Stripe; the forum refers to them by id (rule 40). The ban fee is computed from the
# registry and sent as an amount.
STRIPE_PRICE_MEMBERSHIP = env("STRIPE_PRICE_MEMBERSHIP", default="")
STRIPE_PRICE_GIFT = env("STRIPE_PRICE_GIFT", default="")
STRIPE_PRICE_AVATAR_CAPTION = env("STRIPE_PRICE_AVATAR_CAPTION", default="")

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if env.bool("DJANGO_SECURE", default=False):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
