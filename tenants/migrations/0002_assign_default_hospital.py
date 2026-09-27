"""
Phase 1 data migration: create "Default Hospital" and assign all pre-existing data to it.

Why: the hospital FK was added as NULLABLE first (0001 migrations), so existing
production rows are not blocked. This migration:
  1. Creates Default Hospital (idempotent - slug from settings)
  2. Gives every old row the right hospital:
       - rows with a user link (registered_by / created_by / user / admitted_by
         / received_by) -> that user's hospital
       - child rows (InvoiceItem, PrescriptionItem, Bed) -> the parent's hospital
       - the rest -> Default Hospital
  3. Moves old IDCounter rows to Default Hospital, so numbering like
     PAT-2026-0001 continues FROM WHERE IT LEFT OFF (it does not restart)

Reverse: safe - it just sets hospital back to NULL (no rows are deleted).
No data is deleted/overwritten.
"""
from django.conf import settings
from django.db import migrations

# (app, model, strategy)
#   'default'          -> Default Hospital for all rows
#   ('user', field)    -> that FK's user's hospital, else Default
#   ('parent', field)  -> the parent record's hospital, else Default
TENANT_MODELS = [
    ("accounts", "User", ("user_self", None)),
    ("doctors", "Doctor", ("user", "user")),
    ("patients", "Patient", ("user", "registered_by")),
    ("wards", "Ward", "default"),
    ("wards", "Bed", ("parent", "ward")),
    ("appointments", "Appointment", ("user", "created_by")),
    ("pharmacy", "Medicine", "default"),
    ("pharmacy", "Prescription", ("user", "created_by")),
    ("pharmacy", "PrescriptionItem", ("parent", "prescription")),
    ("billing", "Invoice", ("user", "created_by")),
    ("billing", "InvoiceItem", ("parent", "invoice")),
    ("billing", "Payment", ("user", "received_by")),
    ("wards", "Admission", ("user", "admitted_by")),
    ("core", "AuditLog", ("user", "user")),
]


def get_default_hospital(Hospital):
    slug = getattr(settings, "DEFAULT_HOSPITAL_SLUG", "default")
    name = getattr(settings, "DEFAULT_HOSPITAL_NAME", "Default Hospital")
    hospital, created = Hospital.objects.get_or_create(slug=slug, defaults={"name": name})
    return hospital, created


def assign_from_parent(model, field, default_hospital):
    """
    Give rows their parent/user record's hospital; rows whose parent is NULL or
    whose parent has no hospital get Default Hospital.

    Implementation note: Django allows joined field references in neither
    `.update()` nor `.exclude()` ("Joined field references are not permitted in
    this query"). So we group by parent and run one UPDATE per group - still
    set-based per group, and parent tables are small (doctors / invoices), so
    it is fast.
    """
    pending = model.objects.filter(hospital__isnull=True).exclude(
        **{f"{field}__isnull": True}
    )
    parent_ids = list(
        pending.values_list(f"{field}_id", flat=True).distinct()
    )
    related_model = model._meta.get_field(field).related_model
    for parent_id in parent_ids:
        parent = related_model.objects.filter(pk=parent_id).only("hospital").first()
        hospital = getattr(parent, "hospital", None) or default_hospital
        pending.filter(**{f"{field}_id": parent_id}).update(hospital=hospital)

    # Still NULL (parent NULL / parent's hospital NULL) -> Default
    model.objects.filter(hospital__isnull=True).update(hospital=default_hospital)


def forwards(apps, schema_editor):
    Hospital = apps.get_model("tenants", "Hospital")
    default_hospital, created = get_default_hospital(Hospital)

    total_assigned = 0
    for app_label, model_name, strategy in TENANT_MODELS:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue

        rows = model.objects.filter(hospital__isnull=True)
        count = rows.count()
        if count == 0:
            continue

        if strategy == "default":
            rows.update(hospital=default_hospital)
        elif strategy[0] == "user_self":
            # The users themselves: platform super-admins (is_superuser, no hospital) stay NULL
            model.objects.filter(hospital__isnull=True, is_superuser=False).update(
                hospital=default_hospital
            )
        elif strategy[0] in ("user", "parent"):
            field = strategy[1]
            assign_from_parent(model, field, default_hospital)

        still_null = model.objects.filter(hospital__isnull=True).count()
        total_assigned += count - still_null
        print(
            f"    {app_label}.{model_name}: {count} rows -> hospital assigned "
            f"({still_null} intentionally left NULL)"
        )

    # IDCounter: old counters go to Default Hospital, otherwise numbering restarts at 1
    IDCounter = apps.get_model("core", "IDCounter")
    moved = IDCounter.objects.filter(hospital__isnull=True).update(hospital=default_hospital)
    print(f"    core.IDCounter: {moved} legacy counters -> Default Hospital")
    print(
        f"  Default Hospital: slug='{default_hospital.slug}' "
        f"({'CREATED' if created else 'already existed'}), {total_assigned} rows assigned"
    )


def backwards(apps, schema_editor):
    """Remove the hospital assignment - no rows are deleted. Keep Default Hospital too."""
    for app_label, model_name, _strategy in TENANT_MODELS:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        model.objects.exclude(hospital__isnull=True).update(hospital=None)
    apps.get_model("core", "IDCounter").objects.exclude(hospital__isnull=True).update(hospital=None)


# F is imported here to avoid module-level import order issues
from django.db.models import F as models_F  # noqa: E402


class Migration(migrations.Migration):

    dependencies = [
        # the data backfill runs only after all nullable-FK migrations are applied
        ("tenants", "0001_initial"),
        ("accounts", "0003_alter_user_username"),
        ("core", "0003_auditlog_hospital_idcounter_hospital_and_more"),
        ("patients", "0003_patient_hospital_alter_patient_patient_id_and_more"),
        ("doctors", "0002_doctor_hospital"),
        ("appointments", "0004_appointment_hospital"),
        ("billing", "0003_invoice_hospital_invoiceitem_hospital_and_more"),
        ("pharmacy", "0004_medicine_hospital_prescription_hospital_and_more"),
        ("wards", "0004_admission_hospital_bed_hospital_ward_hospital"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
