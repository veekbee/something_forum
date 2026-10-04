"""The read_only action kind is renamed probation (decided 3 Oct 2026)."""

from django.db import migrations


def forward(apps, schema_editor):
    apps.get_model("moderation", "ModerationAction").objects.filter(kind="read_only").update(kind="probation")


def backward(apps, schema_editor):
    apps.get_model("moderation", "ModerationAction").objects.filter(kind="probation").update(kind="read_only")


class Migration(migrations.Migration):
    dependencies = [("moderation", "0003_action_scope_decline_probation")]

    operations = [migrations.RunPython(forward, backward)]
