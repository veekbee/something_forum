from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    keys = settings.FIELD_ENCRYPTION_KEYS
    if not keys:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not set")
    return MultiFernet([Fernet(k) for k in keys])


class EncryptedTextField(models.TextField):
    """Text encrypted at rest with Fernet. The first key in FIELD_ENCRYPTION_KEY encrypts; every
    listed key can decrypt, so keys can be rotated by putting a new one first."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        return _fernet().decrypt(value.encode()).decode()
