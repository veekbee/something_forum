"""A limited suspension or hold can name several sub-forums (decided 3 Oct 2026). Existing single
scopes are copied into the new many-to-many before the old field goes."""

from django.db import migrations, models


def copy_forward(apps, schema_editor):
    ModerationAction = apps.get_model("moderation", "ModerationAction")
    for action in ModerationAction.objects.exclude(scope_subforum=None):
        action.scope_subforums.add(action.scope_subforum_id)


def copy_backward(apps, schema_editor):
    ModerationAction = apps.get_model("moderation", "ModerationAction")
    for action in ModerationAction.objects.all():
        first = action.scope_subforums.order_by("pk").first()
        if first is not None:
            action.scope_subforum = first
            action.save(update_fields=["scope_subforum"])


class Migration(migrations.Migration):
    dependencies = [
        ("boards", "0005_dm_participation_periods"),
        ("moderation", "0004_rename_read_only_to_probation"),
    ]

    operations = [
        migrations.AddField(
            model_name="moderationaction",
            name="scope_subforums",
            field=models.ManyToManyField(blank=True, related_name="+", to="boards.subforum"),
        ),
        migrations.RunPython(copy_forward, copy_backward),
        migrations.RemoveField(model_name="moderationaction", name="scope_subforum"),
    ]
