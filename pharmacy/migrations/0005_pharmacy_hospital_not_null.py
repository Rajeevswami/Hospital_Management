"""
Phase 1 (step 3 of 3): `hospital` ko NOT NULL karo.

Order matter karta hai:
  000X_*_hospital            -> FK nullable add hua
  tenants.0002_assign_...    -> Default Hospital bana, purana saara data assign hua
  yeh migration              -> ab NOT NULL safe hai

Safety: apply karne se pehle check hota hai ki koi row NULL hospital ke saath na
bachi ho. Agar bachi hai to migration RUK jaati hai aur exact table/row count
batati hai - data chup-chaap galat assign nahi hoga.
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
            "hospital NOT NULL karne se pehle yeh rows Default Hospital ko assign karo: "
            + "; ".join(problems)
            + "  (tenants.0002_assign_default_hospital dobara chalao ya manually set karo)"
        )


def noop(apps, schema_editor):
    pass

MODELS = [("pharmacy", "Medicine"), ("pharmacy", "Prescription"), ("pharmacy", "PrescriptionItem")]


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_assign_default_hospital"),
        ("pharmacy", "0004_medicine_hospital_prescription_hospital_and_more"),
    ]

    operations = [
        migrations.RunPython(verify_no_null_hospital, noop),
        migrations.AlterField(
            model_name='medicine',
            name='hospital',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='%(app_label)s_%(class)s_set', to='tenants.hospital',
                help_text='Tenant - kaunsa hospital yeh record own karta hai',
            ),
        ),
        migrations.AlterField(
            model_name='prescription',
            name='hospital',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='%(app_label)s_%(class)s_set', to='tenants.hospital',
                help_text='Tenant - kaunsa hospital yeh record own karta hai',
            ),
        ),
        migrations.AlterField(
            model_name='prescriptionitem',
            name='hospital',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='%(app_label)s_%(class)s_set', to='tenants.hospital',
                help_text='Tenant - kaunsa hospital yeh record own karta hai',
            ),
        ),
    ]
