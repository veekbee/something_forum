from django.db import migrations


def add(apps, schema_editor):
    """The second paid extra (docs/DESIGN.md, Custom emoji), bought once per emoji."""
    Extra = apps.get_model("billing", "Extra")
    Extra.objects.get_or_create(key="custom_emoji", defaults={
        "name": "Custom emoji", "stripe_price_setting": "STRIPE_PRICE_CUSTOM_EMOJI", "min_role": "provisional",
    })


class Migration(migrations.Migration):
    dependencies = [("billing", "0003_billing_step5")]

    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
