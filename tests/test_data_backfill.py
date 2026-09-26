"""
Phase 1.3 ka data-loss guard.

Production mein multi-tenancy se PEHLE ka data hoga (hospital column NULL).
`tenants.0002_assign_default_hospital` usse "Default Hospital" ko assign karti
hai. Yeh test verify karta hai ki:

  * koi row NULL nahi bachti (silently orphan nahi hoti)
  * child rows parent ka hospital inherit karti hain
  * ID counters Default Hospital pe move hote hain -> PAT-...-0001 sequence
    RESTART nahi hoti, aage se continue hoti hai
  * superuser (platform admin) ka hospital NULL reh jaata hai
  * NOT NULL migration ka safety check NULL row pe RUK jaata hai
"""
import importlib

import pytest
from django.apps import apps as real_apps
from django.db import connection
from django.utils import timezone

from accounts.models import User
from billing.models import Invoice, InvoiceItem
from patients.models import Patient
from tenants.models import Hospital

from .conftest import tenant_scope

pytestmark = pytest.mark.django_db

backfill = importlib.import_module("tenants.migrations.0002_assign_default_hospital")

# Test DB mein saari migrations (NOT NULL dahil) already applied hoti hain.
# Isliye migration executor se schema ko us state pe LE JAATE hain jahan hospital
# column abhi NULLABLE hai (backfill se theek pehle) - production ka exact
# scenario. pytest-django test ke baad transaction rollback karta hai.
PRE_BACKFILL_APPS = ("patients", "billing")
PRE_BACKFILL_STATES = [
    ("patients", "0003_patient_hospital_alter_patient_patient_id_and_more"),
    ("billing", "0003_invoice_hospital_invoiceitem_hospital_and_more"),
]


@pytest.fixture
def legacy_data(transactional_db):
    # transactional_db (na ki db) isliye: yeh fixture migration executor se DDL
    # chalata hai, aur SQLite schema changes ko transaction ke andar allow nahi karta.
    """Multi-tenancy se PEHLE jaisa data: hospital column NULL."""
    from django.apps import apps as _apps
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.migrate(PRE_BACKFILL_STATES)
    _apps.clear_cache()

    now = timezone.now()
    year = now.year
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO core_idcounter (name, value, hospital_id) VALUES (%s, %s, NULL)",
            [f"patient_{year}", 7],
        )
        cur.execute(
            "INSERT INTO core_idcounter (name, value, hospital_id) VALUES (%s, %s, NULL)",
            [f"invoice_{year}", 2],
        )
        for i in range(1, 4):
            cur.execute(
                "INSERT INTO patients_patient "
                "(patient_id, first_name, last_name, date_of_birth, gender, blood_group, "
                " phone, address, emergency_contact_name, emergency_contact_phone, "
                " known_allergies, registered_by_id, created_at, updated_at, hospital_id) "
                # NOTE: timestamps PARAMETER se bhejte hain. datetime('now') SQLite ka
                # function hai - Postgres pe "function datetime(unknown) does not exist".
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,NULL)",
                [f"PAT-{year}-{i:04d}", f"Legacy{i}", "Patient", "1985-01-01", "M", "UNK",
                 f"900000010{i}", "", "", "", "", now, now],
            )
        # legacy patient ka ASLI pk lo (auto-increment state test-order pe depend
        # na kare) aur invoice usi pe lagao
        cur.execute(
            "SELECT id FROM patients_patient WHERE patient_id = %s",
            [f"PAT-{year}-0001"],
        )
        legacy_patient_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO billing_invoice "
            "(invoice_number, patient_id, status, created_by_id, created_at, hospital_id) "
            "VALUES (%s, %s, 'PENDING', NULL, %s, NULL)",
            [f"INV-{year}-0001", legacy_patient_id, now],
        )
        cur.execute("SELECT id FROM billing_invoice WHERE invoice_number = %s", [f"INV-{year}-0001"])
        invoice_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO billing_invoiceitem "
            "(invoice_id, item_type, description, amount, hospital_id) "
            "VALUES (%s, 'OTHER', 'Legacy charge', 250, NULL)",
            [invoice_id],
        )

    yield {"year": year, "patient_id": legacy_patient_id, "invoice_id": invoice_id}

    # Teardown: test ne schema ko pre-backfill state pe chhoda hai. Baaki tests
    # (aur agle test-run) ke liye wapas fully-migrated state pe le jaao. Forward
    # migration ka safety check NULL rows pe RUK jaata hai, isliye pehle legacy
    # rows ko clean kar dete hain (test DB session ke end pe drop ho hi jaata hai).
    from django.db.migrations.loader import MigrationLoader

    with connection.cursor() as cur:
        for table in ("billing_invoiceitem", "billing_invoice", "patients_patient"):
            cur.execute(f"DELETE FROM {table}")

    executor = MigrationExecutor(connection)
    loader = MigrationLoader(connection)
    targets = [loader.graph.leaf_nodes(app)[0] for app in PRE_BACKFILL_APPS]
    executor.migrate(targets)
    _apps.clear_cache()


def _null_counts():
    return {
        "patients": Patient.all_objects.filter(hospital__isnull=True).count(),
        "invoices": Invoice.all_objects.filter(hospital__isnull=True).count(),
        "invoice_items": InvoiceItem.all_objects.filter(hospital__isnull=True).count(),
    }


class TestDataBackfill:
    def test_legacy_rows_start_out_null(self, legacy_data):
        assert _null_counts() == {"patients": 3, "invoices": 1, "invoice_items": 1}

    def test_backfill_assigns_default_hospital_to_every_legacy_row(self, legacy_data):
        backfill.forwards(real_apps, None)

        default = Hospital.objects.get(slug="default")
        assert _null_counts() == {"patients": 0, "invoices": 0, "invoice_items": 0}
        assert Patient.all_objects.filter(hospital=default).count() == 3
        assert InvoiceItem.all_objects.get().hospital_id == default.pk

    def test_backfill_is_idempotent(self, legacy_data):
        backfill.forwards(real_apps, None)
        hospitals = Hospital.objects.count()
        backfill.forwards(real_apps, None)
        assert Hospital.objects.count() == hospitals
        assert _null_counts() == {"patients": 0, "invoices": 0, "invoice_items": 0}

    def test_id_sequence_continues_instead_of_restarting(self, legacy_data, make_hospital):
        """Sabse important data-integrity check: PAT-<year>-0008 aana chahiye, 0001 nahi."""
        year = legacy_data["year"]
        backfill.forwards(real_apps, None)
        default = Hospital.objects.get(slug="default")
        other = make_hospital(name="Naya", slug="naya")

        with tenant_scope(default):
            existing = Patient.all_objects.get(patient_id=f"PAT-{year}-0003")
            fresh = Patient.objects.create(
                first_name="After", last_name="Migration", date_of_birth="1990-01-01",
                gender="F", phone="9000000201",
            )
        assert fresh.patient_id == f"PAT-{year}-0008"
        assert existing.patient_id != fresh.patient_id

        with tenant_scope(other):
            brand_new = Patient.objects.create(
                first_name="Brand", last_name="New", date_of_birth="1990-01-01",
                gender="M", phone="9000000202",
            )
        assert brand_new.patient_id == f"PAT-{year}-0001"

    def test_invoice_number_sequence_also_continues(self, legacy_data):
        year = legacy_data["year"]
        backfill.forwards(real_apps, None)
        default = Hospital.objects.get(slug="default")
        with tenant_scope(default):
            patient = Patient.all_objects.get(patient_id=f"PAT-{year}-0001")
            inv = Invoice.objects.create(patient=patient)
        assert inv.invoice_number == f"INV-{year}-0003"   # 2 ke baad 3


class TestSuperuserAndSafetyCheck:
    def test_superuser_keeps_null_hospital(self, db):
        superuser = User.unscoped.create_superuser(
            username="platform", email="ops@saas.test", password="x"
        )
        assert superuser.hospital_id is None
        backfill.forwards(real_apps, None)
        superuser.refresh_from_db()
        assert superuser.hospital_id is None

    def test_verify_blocks_migration_if_null_rows_remain(self, legacy_data):
        not_null = importlib.import_module("patients.migrations.0004_patient_hospital_not_null")
        with pytest.raises(RuntimeError) as exc:
            not_null.verify_no_null_hospital(real_apps, None)
        assert "patients.Patient" in str(exc.value)
        assert "3 row(s)" in str(exc.value)

    def test_verify_passes_after_backfill(self, legacy_data):
        backfill.forwards(real_apps, None)
        not_null = importlib.import_module("patients.migrations.0004_patient_hospital_not_null")
        not_null.verify_no_null_hospital(real_apps, None)
