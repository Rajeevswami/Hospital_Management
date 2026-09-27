"""
Phase 4 - REST API tests (DRF + JWT + Swagger + tenant isolation).

Every test sends a real HTTP request (Django test client), so middleware,
JWT auth, permissions, serializers - everything actually runs.

NOTE: always pass `HTTP_HOST="<slug>.testserver"` to the test client. A full URL
("http://acme.testserver/...") makes the Django client send the default HTTP_HOST
sends "testserver" and the subdomain does not resolve (this is a client quirk,
it does not happen in production).
"""
import datetime as dt

import pytest
from django.test import Client
from django.utils import timezone

from accounts.models import User
from appointments.models import Appointment
from doctors.models import Doctor
from ml_engine.models import AppointmentRisk
from patients.models import Patient
from subscriptions.models import Feature, Plan, Subscription
from tenants.models import Hospital

from .conftest import tenant_scope

pytestmark = pytest.mark.django_db

PASSWORD = "TestPass!123"
HOST_A = "acme.testserver"
HOST_B = "beta-city.testserver"


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def api_plan(db):
    """Scale plan - api_access ON."""
    return Plan.objects.create(
        name="Scale", code="scale", price=4999, patient_limit=100, staff_limit=100,
        features_json={Feature.API_ACCESS: True, Feature.AI_NO_SHOW: True},
    )


@pytest.fixture
def free_plan(db):
    return Plan.objects.create(
        name="Free", code="free", price=0, patient_limit=2, staff_limit=2, features_json={},
    )


def subscribe(hospital, plan, days=30):
    now = timezone.now()
    return Subscription.all_objects.create(
        hospital=hospital, plan=plan, status=Subscription.Status.ACTIVE,
        current_period_start=now, current_period_end=now + timezone.timedelta(days=days),
    )


@pytest.fixture
def api_setup(hospital_a, hospital_b, api_plan, make_user):
    """Two hospitals, both on the Scale plan, with staff + data."""
    subscribe(hospital_a, api_plan)
    subscribe(hospital_b, api_plan)

    users = {"hospital_a": hospital_a, "hospital_b": hospital_b}
    with tenant_scope(hospital_a):
        users["admin_a"] = make_user(hospital_a, username="api.admin",
                                     role=User.Role.ADMIN, password=PASSWORD)
        users["recep_a"] = make_user(hospital_a, username="api.recep",
                                     role=User.Role.RECEPTIONIST, password=PASSWORD)
        doc_user = make_user(hospital_a, username="api.doc",
                             role=User.Role.DOCTOR, password=PASSWORD)
        users["doctor_a"] = doc_user
        users["doctor_a_profile"] = Doctor.objects.create(
            user=doc_user, specialization="General", hospital=hospital_a)
        users["patient_a"] = Patient.objects.create(
            first_name="Asha", last_name="Verma", date_of_birth=dt.date(1991, 4, 2),
            gender="F", phone="9811100001", hospital=hospital_a)
        users["appt_a"] = Appointment.objects.create(
            patient=users["patient_a"], doctor=users["doctor_a_profile"],
            appointment_date=timezone.now().date(), appointment_time=dt.time(10, 0),
            reason="Fever", hospital=hospital_a)

    with tenant_scope(hospital_b):
        # DELIBERATELY the same username + same password: this is exactly how a real cross-tenant leak would happen
        users["admin_b"] = make_user(hospital_b, username="api.admin",
                                     role=User.Role.ADMIN, password=PASSWORD)
        users["patient_b"] = Patient.objects.create(
            first_name="Bhola", last_name="Singh", date_of_birth=dt.date(1988, 7, 9),
            gender="M", phone="9811100002", hospital=hospital_b)
    return users


def get_token(client, host, username, password=PASSWORD):
    resp = client.post("/api/token/", {"username": username, "password": password},
                       HTTP_HOST=host)
    assert resp.status_code == 200, resp.content
    return resp.json()["access"]


def auth(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


# --------------------------------------------------------------------- auth
class TestJwtAuth:
    def test_token_endpoint_returns_jwt_with_tenant_claims(self, client, api_setup):
        resp = client.post("/api/token/", {"username": "api.admin", "password": PASSWORD},
                           HTTP_HOST=HOST_A)
        assert resp.status_code == 200
        body = resp.json()
        assert "access" in body and "refresh" in body
        assert body["user"]["username"] == "api.admin"
        assert body["user"]["hospital"] == "acme"
        assert body["user"]["role"] == User.Role.ADMIN

    def test_wrong_password_rejected(self, client, api_setup):
        resp = client.post("/api/token/", {"username": "api.admin", "password": "WrongPass!1"},
                           HTTP_HOST=HOST_A)
        assert resp.status_code == 401

    def test_unknown_username_rejected(self, client, api_setup):
        resp = client.post("/api/token/", {"username": "ghost", "password": PASSWORD},
                           HTTP_HOST=HOST_A)
        assert resp.status_code == 401

    def test_same_username_in_other_hospital_gets_its_own_token(self, client, api_setup):
        """
        Both hospitals have 'api.admin'. Whoever logs in on A's subdomain
        must be A's admin, not B's.
        """
        resp = client.post("/api/token/", {"username": "api.admin", "password": PASSWORD},
                           HTTP_HOST=HOST_B)
        assert resp.status_code == 200
        assert resp.json()["user"]["hospital"] == "beta-city"
        assert resp.json()["user"]["id"] == api_setup["admin_b"].pk

    def test_token_works_on_api(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/me/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        assert resp.json()["username"] == "api.admin"

    def test_token_of_other_hospital_rejected(self, client, api_setup):
        """A's token on B's subdomain -> 401 (the JWT user lookup is tenant-scoped)."""
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/me/", HTTP_HOST=HOST_B, **auth(token))
        assert resp.status_code == 401

    def test_refresh_token_works(self, client, api_setup):
        resp = client.post("/api/token/", {"username": "api.admin", "password": PASSWORD},
                           HTTP_HOST=HOST_A)
        refresh = resp.json()["refresh"]
        resp2 = client.post("/api/token/refresh/", {"refresh": refresh}, HTTP_HOST=HOST_A)
        assert resp2.status_code == 200
        assert "access" in resp2.json()

    def test_no_token_gives_401(self, client, api_setup):
        resp = client.get("/api/patients/", HTTP_HOST=HOST_A)
        assert resp.status_code == 401

    def test_tampered_token_rejected(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/me/", HTTP_HOST=HOST_A, **auth(token + "x"))
        assert resp.status_code == 401

    def test_token_without_tenant_gives_401_json(self, client, api_setup):
        """API call on the root domain (no subdomain) -> clean JSON 401, not an HTML redirect."""
        resp = client.post("/api/token/", {"username": "api.admin", "password": PASSWORD})
        assert resp.status_code == 401
        assert resp["Content-Type"].startswith("application/json")
        assert "X-Hospital-Slug" in resp.json()["detail"]


# ------------------------------------------------------------------ tenant
class TestTenantIsolation:
    def test_patients_list_only_own_hospital(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/patients/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        names = [p["first_name"] for p in resp.json()["results"]]
        assert "Asha" in names
        assert "Bhola" not in names       # hospital B's patient did not leak

    def test_other_hospital_patient_detail_404(self, client, api_setup):
        """B's patient id requested on A's API -> 404 (not even the existence is revealed)."""
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get(f"/api/patients/{api_setup['patient_b'].pk}/",
                          HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 404

    def test_cannot_use_foreign_patient_fk(self, client, api_setup):
        token = get_token(client, HOST_A, "api.recep")
        resp = client.post(
            "/api/appointments/",
            {"patient": api_setup["patient_b"].pk,
             "doctor": api_setup["doctor_a_profile"].pk,
             "appointment_date": (timezone.now() + timezone.timedelta(days=3)).date().isoformat(),
             "appointment_time": "11:00", "reason": "Checkup"},
            content_type="application/json", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 400
        assert "patient" in resp.json()

    def test_hospital_field_cannot_be_spoofed(self, client, api_setup):
        token = get_token(client, HOST_A, "api.recep")
        resp = client.post(
            "/api/patients/",
            {"first_name": "Spoof", "date_of_birth": "1990-01-01", "gender": "M",
             "phone": "9811100009", "hospital": api_setup["hospital_b"].pk},
            content_type="application/json", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 201, resp.content
        with tenant_scope(api_setup["hospital_a"]):
            created = Patient.objects.get(first_name="Spoof")
        assert created.hospital_id == api_setup["hospital_a"].pk   # it stayed in A

    def test_x_hospital_slug_header_resolves_tenant(self, client, api_setup):
        """Tenant via header without a subdomain (for localhost/Postman/mobile)."""
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/patients/", HTTP_X_HOSPITAL_SLUG="acme", **auth(token))
        assert resp.status_code == 200
        assert "Asha" in [p["first_name"] for p in resp.json()["results"]]

    def test_x_hospital_slug_cannot_switch_tenant(self, client, api_setup):
        """
        A's user asking for B's tenant via the header -> the request must be stopped.

        401 (not 403) because: the JWT user lookup itself goes through the
        tenant-scoped manager, so A's user is simply not found in B's tenant ->
        authentication fails. In neither case does data leak.
        """
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/patients/", HTTP_X_HOSPITAL_SLUG="beta-city", **auth(token))
        assert resp.status_code in (401, 403)
        assert "Bhola" not in resp.content.decode()


# ------------------------------------------------------------------ RBAC
class TestRbac:
    def test_doctor_cannot_create_patient(self, client, api_setup):
        token = get_token(client, HOST_A, "api.doc")
        resp = client.post("/api/patients/",
                           {"first_name": "No", "date_of_birth": "1990-01-01",
                            "gender": "M", "phone": "9811100010"},
                           content_type="application/json", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 403

    def test_doctor_can_read_patients(self, client, api_setup):
        token = get_token(client, HOST_A, "api.doc")
        assert client.get("/api/patients/", HTTP_HOST=HOST_A, **auth(token)).status_code == 200

    def test_receptionist_can_create_patient(self, client, api_setup):
        token = get_token(client, HOST_A, "api.recep")
        resp = client.post("/api/patients/",
                           {"first_name": "New", "last_name": "Patient",
                            "date_of_birth": "1995-05-05", "gender": "M",
                            "phone": "9811100011"},
                           content_type="application/json", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 201, resp.content
        assert resp.json()["patient_id"]          # a per-hospital ID was created

    def test_patient_update_allowed_for_receptionist(self, client, api_setup):
        token = get_token(client, HOST_A, "api.recep")
        resp = client.patch(f"/api/patients/{api_setup['patient_a'].pk}/",
                            {"phone": "9999900000"}, content_type="application/json",
                            HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        assert resp.json()["phone"] == "9999900000"

    def test_staff_endpoint_admin_only(self, client, api_setup):
        recep = get_token(client, HOST_A, "api.recep")
        assert client.get("/api/staff/", HTTP_HOST=HOST_A, **auth(recep)).status_code == 403
        admin = get_token(client, HOST_A, "api.admin")
        assert client.get("/api/staff/", HTTP_HOST=HOST_A, **auth(admin)).status_code == 200

    def test_invoices_read_only(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        assert client.get("/api/invoices/", HTTP_HOST=HOST_A, **auth(token)).status_code == 200
        resp = client.post("/api/invoices/", {}, HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 405

    def test_doctor_sees_only_own_appointments(self, client, api_setup, make_user):
        with tenant_scope(api_setup["hospital_a"]):
            other_doc_user = make_user(api_setup["hospital_a"], username="api.doc2",
                                       role=User.Role.DOCTOR, password=PASSWORD)
            other_doc = Doctor.objects.create(user=other_doc_user, specialization="Cardio")
            other_patient = Patient.objects.create(
                first_name="Doosra", date_of_birth=dt.date(1993, 1, 1),
                gender="M", phone="9811100012", hospital=api_setup["hospital_a"])
            Appointment.objects.create(
                patient=other_patient, doctor=other_doc,
                appointment_date=timezone.now().date(), appointment_time=dt.time(12, 0),
                hospital=api_setup["hospital_a"])

        token = get_token(client, HOST_A, "api.doc")
        resp = client.get("/api/appointments/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        patients = [a["patient_name"] for a in resp.json()["results"]]
        assert "Asha Verma" in patients
        assert "Doosra" not in patients


# ------------------------------------------------------------ plan gating
class TestPlanGating:
    def test_api_returns_402_without_api_access(self, client, hospital_a, free_plan, make_user):
        make_user(hospital_a, username="free.admin", role=User.Role.ADMIN, password=PASSWORD)
        subscribe(hospital_a, free_plan)

        c = Client()
        token = get_token(c, HOST_A, "free.admin")
        resp = c.get("/api/patients/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 402
        body = resp.json()
        assert body["reason"] == "not_in_plan"
        assert body["feature"] == Feature.API_ACCESS

    def test_token_still_issued_without_api_access(self, client, hospital_a, free_plan, make_user):
        """A token must be issued - otherwise a client would not even discover the API."""
        make_user(hospital_a, username="free.admin2", role=User.Role.ADMIN, password=PASSWORD)
        subscribe(hospital_a, free_plan)
        resp = client.post("/api/token/", {"username": "free.admin2", "password": PASSWORD},
                           HTTP_HOST=HOST_A)
        assert resp.status_code == 200

    def test_no_subscription_gives_402(self, client, hospital_a, make_user):
        make_user(hospital_a, username="nosub.admin", role=User.Role.ADMIN, password=PASSWORD)
        c = Client()
        token = get_token(c, HOST_A, "nosub.admin")
        resp = c.get("/api/patients/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 402
        assert resp.json()["reason"] == "no_subscription"

    def test_patient_limit_enforced_via_api(self, client, api_setup):
        with tenant_scope(api_setup["hospital_a"]):
            sub = Subscription.for_hospital(api_setup["hospital_a"])
            sub.plan.patient_limit = 1
            sub.plan.save()

        token = get_token(client, HOST_A, "api.recep")
        resp = client.post("/api/patients/",
                           {"first_name": "Extra", "date_of_birth": "1990-01-01",
                            "gender": "F", "phone": "9811100013"},
                           content_type="application/json", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 402
        assert resp.json()["reason"] == "limit_reached"


# ------------------------------------------------------------- resources
class TestResources:
    def test_appointments_today(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/appointments/today/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        assert any(a["id"] == api_setup["appt_a"].pk for a in resp.json()["results"])

    def test_appointment_status_update(self, client, api_setup):
        token = get_token(client, HOST_A, "api.recep")
        resp = client.patch(f"/api/appointments/{api_setup['appt_a'].pk}/status/",
                            {"status": "NO_SHOW"}, content_type="application/json",
                            HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200, resp.content
        assert resp.json()["status"] == "NO_SHOW"
        api_setup["appt_a"].refresh_from_db()
        assert api_setup["appt_a"].status == "NO_SHOW"

    def test_invalid_status_rejected(self, client, api_setup):
        token = get_token(client, HOST_A, "api.recep")
        resp = client.patch(f"/api/appointments/{api_setup['appt_a'].pk}/status/",
                            {"status": "BANANA"}, content_type="application/json",
                            HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 400

    def test_appointment_risk_endpoint(self, client, api_setup):
        from ml_engine.predict import score_appointment

        with tenant_scope(api_setup["hospital_a"]):
            score_appointment(api_setup["appt_a"])
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get(f"/api/appointments/{api_setup['appt_a'].pk}/risk/",
                          HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        assert 0 <= resp.json()["score"] <= 1

    def test_appointment_risk_404_when_unscored(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        with tenant_scope(api_setup["hospital_a"]):
            AppointmentRisk.all_objects.filter(appointment=api_setup["appt_a"]).delete()
        resp = client.get(f"/api/appointments/{api_setup['appt_a'].pk}/risk/",
                          HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 404

    def test_risks_high_endpoint(self, client, api_setup):
        from ml_engine.predict import score_appointment

        with tenant_scope(api_setup["hospital_a"]):
            score_appointment(api_setup["appt_a"])
            risk = api_setup["appt_a"].risk
            risk.level = AppointmentRisk.Level.HIGH
            risk.save()
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/risks/high/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        assert resp.json()          # at least one row

    def test_patient_nested_appointments(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get(f"/api/patients/{api_setup['patient_a'].pk}/appointments/",
                          HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        assert resp.json()["results"]

    def test_search_and_pagination_shape(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/patients/?search=Asha&page_size=1",
                          HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert {"count", "next", "previous", "results"} <= set(body)
        assert body["count"] == 1

    def test_ordering_param(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/patients/?ordering=first_name", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200

    def test_invalid_filter_value_gives_400(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/appointments/?doctor=not-a-number",
                          HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 400

    def test_doctors_endpoint(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/doctors/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        assert resp.json()["results"][0]["name"].startswith("Dr.") or             resp.json()["results"][0]["name"]


# --------------------------------------------------------------- context
class TestContextEndpoints:
    def test_hospital_endpoint(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/hospital/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "acme"
        assert body["plan"] == "Scale"
        assert body["subscription_status"] == Subscription.Status.ACTIVE

    def test_subscription_endpoint(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/subscription/", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_accessible"] is True
        assert body["features"][Feature.API_ACCESS] is True

    def test_feature_check_allowed_and_denied(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        ok = client.get("/api/features/?feature=api_access", HTTP_HOST=HOST_A, **auth(token))
        assert ok.status_code == 200 and ok.json()["allowed"] is True
        denied = client.get("/api/features/?feature=multi_branch", HTTP_HOST=HOST_A, **auth(token))
        assert denied.status_code == 402 and denied.json()["allowed"] is False

    def test_feature_check_unknown_feature(self, client, api_setup):
        token = get_token(client, HOST_A, "api.admin")
        resp = client.get("/api/features/?feature=bogus", HTTP_HOST=HOST_A, **auth(token))
        assert resp.status_code == 400

    def test_api_root_lists_endpoints(self, client, api_setup):
        resp = client.get("/api/", HTTP_HOST=HOST_A)
        assert resp.status_code == 200
        body = resp.json()
        for key in ("token", "patients", "appointments", "docs", "schema"):
            assert key in body


# ----------------------------------------------------------------- docs
class TestDocs:
    def test_swagger_ui_loads(self, client, api_setup):
        resp = client.get("/api/docs/", HTTP_HOST=HOST_A)
        assert resp.status_code == 200
        assert b"swagger" in resp.content.lower()

    def test_openapi_schema_generated(self, client, api_setup):
        resp = client.get("/api/schema/", HTTP_HOST=HOST_A, HTTP_ACCEPT="application/json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "Hospital Management SaaS API"
        assert "/api/patients/" in schema["paths"]
        assert "/api/token/" in schema["paths"]
        schemes = schema["components"]["securitySchemes"]
        bearer = [s for s in schemes.values()
                  if s.get("type") == "http" and s.get("scheme") == "bearer"]
        assert bearer, f"the JWT bearer scheme is not documented: {schemes}"

    def test_spectacular_management_command_validates(self, tmp_path):
        """drf-spectacular's own validator - the schema is OpenAPI-spec valid."""
        from django.core.management import call_command

        out = tmp_path / "schema.yml"
        call_command("spectacular", "--file", str(out), "--validate")
        assert out.stat().st_size > 1000
        assert "Hospital Management SaaS API" in out.read_text()
