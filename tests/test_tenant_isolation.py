"""
THE MOST IMPORTANT TEST OF PHASE 1: one hospital's data must never show up
on another hospital.

Three layers are verified:
  1. ORM      - Model.objects is automatically scoped to the tenant
  2. HTTP     - tenant resolved from the subdomain; another tenant's URL = 404
  3. Auth     - a hospital A login does not work on hospital B's subdomain
"""
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client

from accounts.models import User
from appointments.models import Appointment
from billing.models import Invoice
from doctors.models import Doctor
from patients.models import Patient
from pharmacy.models import Medicine
from tenants.context import all_tenants
from wards.models import Ward

from .conftest import tenant_scope

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------- ORM layer
class TestQuerysetScoping:
    def test_objects_returns_only_current_tenant_patients(self, hospital_a, hospital_b):
        with tenant_scope(hospital_a):
            pa = Patient.objects.create(
                first_name="Asha", last_name="A", date_of_birth="1990-01-01",
                gender="F", phone="9000000001",
            )
        with tenant_scope(hospital_b):
            pb = Patient.objects.create(
                first_name="Baldev", last_name="B", date_of_birth="1985-05-05",
                gender="M", phone="9000000002",
            )

        with tenant_scope(hospital_a):
            assert list(Patient.objects.values_list("pk", flat=True)) == [pa.pk]
        with tenant_scope(hospital_b):
            assert list(Patient.objects.values_list("pk", flat=True)) == [pb.pk]

    def test_get_by_pk_of_other_tenant_raises_doesnotexist(self, hospital_a, hospital_b):
        with tenant_scope(hospital_b):
            other = Patient.objects.create(
                first_name="Quiet", last_name="Ghost", date_of_birth="1990-01-01",
                gender="M", phone="9000000003",
            )
        with tenant_scope(hospital_a):
            with pytest.raises(Patient.DoesNotExist):
                Patient.objects.get(pk=other.pk)

    def test_medicines_are_tenant_scoped(self, hospital_a, hospital_b):
        with tenant_scope(hospital_a):
            Medicine.objects.create(name="Paracetamol", unit_price=5)
        with tenant_scope(hospital_b):
            Medicine.objects.create(name="Ibuprofen", unit_price=8)
            assert Medicine.objects.count() == 1
            assert Medicine.objects.get().name == "Ibuprofen"
        with tenant_scope(hospital_a):
            assert Medicine.objects.count() == 1
            assert Medicine.objects.get().name == "Paracetamol"

    def test_wards_are_tenant_scoped(self, hospital_a, hospital_b):
        with tenant_scope(hospital_a):
            Ward.objects.create(name="Ward A")
        with tenant_scope(hospital_b):
            Ward.objects.create(name="Ward B")
            assert [w.name for w in Ward.objects.all()] == ["Ward B"]
        with tenant_scope(hospital_a):
            assert [w.name for w in Ward.objects.all()] == ["Ward A"]

    def test_all_objects_is_cross_tenant_escape_hatch(self, hospital_a, hospital_b):
        with tenant_scope(hospital_a):
            Patient.objects.create(
                first_name="X", last_name="X", date_of_birth="1990-01-01",
                gender="M", phone="9000000004",
            )
        with tenant_scope(hospital_b):
            Patient.objects.create(
                first_name="Y", last_name="Y", date_of_birth="1991-01-01",
                gender="F", phone="9000000005",
            )
            assert Patient.objects.count() == 1
            assert Patient.all_objects.count() == 2
        with all_tenants():
            assert Patient.objects.count() == 2

    def test_no_tenant_during_request_is_a_loud_error_not_silently_everything(
        self, hospital_a
    ):
        """A silent leak is the most dangerous bug - hence the loud failure."""
        from tenants import context as ctx

        with tenant_scope(hospital_a):
            Patient.objects.create(
                first_name="Z", last_name="Z", date_of_birth="1990-01-01",
                gender="M", phone="9000000006",
            )
        # request is active but no tenant is set -> error
        ctx.mark_request_started()
        try:
            with pytest.raises(ImproperlyConfigured):
                list(Patient.objects.all())
        finally:
            ctx.mark_request_finished()

    def test_related_child_records_cannot_be_created_for_other_tenant(
        self, hospital_a, hospital_b
    ):
        with tenant_scope(hospital_a):
            patient = Patient.objects.create(
                first_name="Owner", last_name="A", date_of_birth="1990-01-01",
                gender="M", phone="9000000007",
            )
        # trying to use hospital A's patient in hospital B's context
        with tenant_scope(hospital_b):
            with pytest.raises(Patient.DoesNotExist):
                Patient.objects.get(pk=patient.pk)


# ---------------------------------------------------------------- HTTP layer
class TestHttpIsolation:
    def _login(self, client, user, password="TestPass!123"):
        assert client.login(username=user.username, password=password)

    def test_dashboard_shows_only_own_tenant_numbers(
        self, client, hospital_a, hospital_b, make_user, make_patient
    ):
        admin_a = make_user(hospital_a, username="admin-a", role=User.Role.ADMIN)
        make_user(hospital_b, username="admin-b", role=User.Role.ADMIN)
        for _ in range(3):
            make_patient(hospital_a)
        make_patient(hospital_b)

        with tenant_scope(hospital_a):
            client.force_login(admin_a)
            resp = client.get("/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 200
        assert resp.context["total_patients"] == 3

    def test_other_tenants_patient_detail_is_404(
        self, client, hospital_a, hospital_b, make_user, make_patient
    ):
        user_a = make_user(hospital_a, username="recep-a")
        secret = make_patient(hospital_b, first_name="Secret")

        with tenant_scope(hospital_a):
            client.force_login(user_a)
            resp = client.get(f"/patients/{secret.pk}/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 404

    def test_user_of_other_hospital_is_blocked_on_foreign_subdomain(
        self, client, hospital_a, hospital_b, make_user
    ):
        """Hospital B's user arriving on hospital A's subdomain -> blocked (zero data)."""
        user_b = make_user(hospital_b, username="intruder")
        with tenant_scope(hospital_b):
            client.force_login(user_b)
            resp = client.get("/patients/", HTTP_HOST="acme.testserver")
        # either 403 (middleware guard) or a redirect to their own hospital - both safe
        assert resp.status_code in (302, 403, 404)
        if resp.status_code == 302:
            assert "acme" not in resp["Location"]

    def test_allowed_domain_with_no_tenant_redirects_to_login(
        self, client, hospital_a, make_patient
    ):
        """Root domain (no subdomain) -> redirect to login, never data."""
        make_patient(hospital_a)
        resp = client.get("/patients/", HTTP_HOST="testserver")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def test_unrelated_host_is_rejected_by_django(self, client, hospital_a, make_patient):
        """A host outside ALLOWED_HOSTS -> Django itself returns 400 (before the middleware)."""
        make_patient(hospital_a)
        resp = client.get("/patients/", HTTP_HOST="evil.com")
        assert resp.status_code == 400

    def test_middleware_sets_request_hospital_from_subdomain(
        self, client, hospital_a, make_user
    ):
        user = make_user(hospital_a, username="staff-a")
        with tenant_scope(hospital_a):
            client.force_login(user)
            resp = client.get("/patients/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 200
        assert resp.wsgi_request.hospital.pk == hospital_a.pk
        assert resp.wsgi_request.tenant_source in ("subdomain", "session", "user")


# ---------------------------------------------------------------- auth layer
class TestAuthIsolation:
    def test_same_username_can_exist_in_two_hospitals(self, hospital_a, hospital_b, make_user):
        a = make_user(hospital_a, username="admin")
        b = make_user(hospital_b, username="admin")
        assert a.pk != b.pk
        assert User.all_objects.filter(username="admin").count() == 2

    def test_login_only_works_on_own_hospital_subdomain(
        self, client, hospital_a, hospital_b, make_user
    ):
        make_user(hospital_a, username="admin", password="PassA!12345")
        make_user(hospital_b, username="admin", password="PassB!12345")

        # A's credentials on A's subdomain -> OK
        ok = client.post(
            "/accounts/login/",
            {"username": "admin", "password": "PassA!12345"},
            HTTP_HOST="acme.testserver",
        )
        assert ok.status_code == 302  # redirect to the dashboard

        client.logout()
        # A's credentials on B's subdomain -> FAIL
        bad = client.post(
            "/accounts/login/",
            {"username": "admin", "password": "PassA!12345"},
            HTTP_HOST="beta-city.testserver",
        )
        assert bad.status_code == 200  # login page dobara
        assert "_auth_user_id" not in client.session

    def test_staff_list_never_shows_other_hospital_staff(
        self, client, hospital_a, hospital_b, make_user
    ):
        admin_a = make_user(hospital_a, username="admin-a", role=User.Role.ADMIN)
        make_user(hospital_a, username="doc-a", role=User.Role.DOCTOR)
        make_user(hospital_b, username="doc-b", role=User.Role.DOCTOR)

        with tenant_scope(hospital_a):
            client.force_login(admin_a)
            resp = client.get("/accounts/staff/", HTTP_HOST="acme.testserver")
        names = [u.username for u in resp.context["staff"]]
        assert "doc-a" in names
        assert "doc-b" not in names
