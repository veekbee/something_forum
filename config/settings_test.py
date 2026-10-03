"""Test settings: fixed throwaway secrets so the suite runs without a .env file."""

import os

from cryptography.fernet import Fernet

os.environ["DJANGO_READ_DOT_ENV"] = "false"
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-secret")
os.environ.setdefault("DATABASE_URL", "postgres://forum:forum@localhost:5432/forum")

from config.settings import *  # noqa: E402,F403

FIELD_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
