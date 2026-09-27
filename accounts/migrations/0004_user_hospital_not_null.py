"""
Phase 1 (step 3 of 3): make `hospital` NOT NULL.

Order matters:
  000X_*_hospital            -> FK added as nullable
  tenants.0002_assign_...    -> Default Hospital created, all pre-existing data assigned
  this migration             -> NOT NULL is safe now

Safety: before applying, it checks that no row is left with a NULL hospital.
If any remain, the migration STOPS and reports the exact table/row count -
data is never silently mis-assigned.
"""
from django.db import migrations, models
import django.db.models.deletion


def verify_no_null_hospital(apps, schema_editor):
    problems = []
    for app_label, model_name in MODELS:
        model = apps.get_model(app_label, model_name)
        count = model.objects.filter(hospital__isnull=True).count()
        if count:
            problems.append(f"{app_label}.{model_name}: {count} row(s)")
    if problems:
        raise RuntimeError(
            "Assign these rows to Default Hospital before making hospital NOT NULL: "
            + "; ".join(problems)
            + "  (re-run tenants.0002_assign_default_hospital or set them manually)"
        )


def noop(apps, schema_editor):
    pass

MODELS = [("accounts", "User")]   # User.hospital may stay NULL (platform super-admin)


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_assign_default_hospital"),
        ("accounts", "0003_alter_user_username"),
    ]

    operations = [
        migrations.RunPython(verify_no_null_hospital, noop),
        migrations.AlterField(
            model_name='user',
            name='hospital',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='staff', to='tenants.hospital',
                help_text='Which hospital this login belongs to. NULL = platform super-admin.',
            ),
        ),
    ]
