"""Rename the stored links value members_only to full_and_above (decided 3 Oct 2026)."""

from django.db import migrations

KEY = "subforum.links"


def rename(apps, old, new):
    SubForum = apps.get_model("boards", "SubForum")
    for subforum in SubForum.objects.all():
        if subforum.settings.get(KEY) == old:
            subforum.settings[KEY] = new
            subforum.save(update_fields=["settings"])


def forward(apps, schema_editor):
    rename(apps, "members_only", "full_and_above")


def backward(apps, schema_editor):
    rename(apps, "full_and_above", "members_only")


class Migration(migrations.Migration):
    dependencies = [("boards", "0002_forum_pages")]

    operations = [migrations.RunPython(forward, backward)]
