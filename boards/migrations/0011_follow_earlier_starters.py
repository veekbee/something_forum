from django.db import migrations


def forwards(apps, schema_editor):
    """Starters follow the threads they started before following existed (decided 4 Oct 2026)."""
    from boards.follows import follow_earlier_starters

    follow_earlier_starters(apps.get_model("boards", "Thread"), apps.get_model("boards", "ThreadParticipant"))


class Migration(migrations.Migration):
    dependencies = [("boards", "0010_thread_read")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
