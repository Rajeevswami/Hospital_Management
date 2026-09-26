"""
Phase 1 data migration: "Default Hospital" banao aur purana saara data usse assign karo.

Kyun: hospital FK pehle NULLABLE add kiya gaya hai (0001 migrations), taaki existing
production rows block na hon. Yeh migration:
  1. Default Hospital create karti hai (idempotent - slug settings se)
  2. Har purani row ko sahi hospital deti hai:
       - jinka koi user link hai (registered_by / created_by / user / admitted_by
         / received_by) -> us user ka hospital
       - child rows (InvoiceItem, PrescriptionItem, Bed) -> parent ka hospital
       - baaki -> Default Hospital
  3. IDCounter ke purane rows Default Hospital pe move karti hai, taaki
     PAT-2026-0001 jaisi numbering AAGE SE continue ho (restart nahi hoti)

Reverse: safe - bas hospital ko wapas NULL kar deta hai (koi row delete nahi hoti).
Koi data delete/overwrite nahi hota.
"""
from django.conf import settings
from django.db import migrations

# (app, model, strategy)
#   'default'          -> sab rows ko Default Hospital
#   ('user', field)    -> us FK ke user ka hospital, warna Default
#   ('parent', field)  -> parent record ka hospital, warna Default
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
    Rows ko unke parent/user record ka hospital do; jinke parent NULL hai ya
    parent ka hospital set nahi, unhe Default Hospital.

    Implementation note: Django `.update()` aur `.exclude()` dono mein joined
    field references allow nahi karta ("Joined field references are not
    permitted in this query"). Isliye parent-wise group karke ek-ek UPDATE
    chalate hain - still set-based per group, aur parent tables chhoti hoti hain
    (doctors / invoices), isliye fast.
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

    # Jo abhi bhi NULL hain (parent NULL / parent ka hospital NULL) -> Default
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
            # User khud: platform super-admin (is_superuser, koi hospital nahi) NULL rehne do
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

    # IDCounter: purane counters Default Hospital pe, warna numbering 1 se restart hogi
    IDCounter = apps.get_model("core", "IDCounter")
    moved = IDCounter.objects.filter(hospital__isnull=True).update(hospital=default_hospital)
    print(f"    core.IDCounter: {moved} legacy counters -> Default Hospital")
    print(
        f"  Default Hospital: slug='{default_hospital.slug}' "
        f"({'CREATED' if created else 'already existed'}), {total_assigned} rows assigned"
    )


def backwards(apps, schema_editor):
    """Hospital assignment hata do - rows delete nahi hoti. Default Hospital bhi rehne do."""
    for app_label, model_name, _strategy in TENANT_MODELS:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        model.objects.exclude(hospital__isnull=True).update(hospital=None)
    apps.get_model("core", "IDCounter").objects.exclude(hospital__isnull=True).update(hospital=None)


# F import yahan, taaki module-level import order ka issue na ho
from django.db.models import F as models_F  # noqa: E402


class Migration(migrations.Migration):

    dependencies = [
        # Saare nullable-FK migrations apply hone ke baad hi data backfill
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
