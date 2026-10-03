from django.core.exceptions import ValidationError


class HistoryRowMixin:
    """For history tables (role assignments, sponsorships): once a row exists, only the fields
    named in `closing_fields` may change, and each only from empty to a value. Changing a role
    or a sponsor means closing the row and adding a new one (design rule 5)."""

    closing_fields: tuple = ()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)
            for field in self._meta.concrete_fields:
                old = getattr(original, field.attname)
                new = getattr(self, field.attname)
                if old == new:
                    continue
                if field.name in self.closing_fields and old in (None, ""):
                    continue
                raise ValidationError(
                    f"{self._meta.label}.{field.name} cannot be changed; close this row and add a new one"
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self._meta.label} rows are history and cannot be deleted")
