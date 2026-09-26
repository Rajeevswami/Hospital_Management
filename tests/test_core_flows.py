"""
Core flows multi-tenant duniya mein bhi kaam kar rahe hain ya nahi:
appointment booking, billing/PDF, RBAC, per-hospital ID sequence.
"""
import datetime as dt

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from appointments.models import Appointment
from billing.models import Invoice, InvoiceItem, Payment
from doctors.models import Doctor
from patients.models import Patient
from wards.models import Admission, Bed, Ward

from .conftest import tenant_scope

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup_hospital(hospital_a, make_user):
    """Hospital A ke andar ek complete mini-hospital."""
    admin = make_user(hospital_a, username="admin-a", role=User.Role.ADMIN)
    doctor_user = make_user(hospital_a, username="doc-a", role=User.Role.DOCTOR)
    receptionist = make_user(hospital_a, username="rec-a", role=User.Role.RECEPTIONIST)
    with tenant_scope(hospital_a):
        doctor = Doctor.objects.create(user=doctor_user, specialization="General Medicine")
        patient = Patient.objects.create(
            first_name="Ramesh", last_name="Kumar", date_of_birth="1988-03-12",
            gender="M", phone="9812345678",
        )
    return {
        "hospital": hospital_a, "admin": admin, "doctor_user": doctor_user,
        "doctor": doctor, "patient": patient, "receptionist": receptionist,
    }


class TestPerHospitalIdSequence:
    def test_two_hospitals_both_start_from_0001(self, hospital_a, hospital_b, make_patient):
        with tenant_scope(hospital_a):
            pa = Patient.objects.create(
                first_name="A1", last_name="X", date_of_birth="1990-01-01",
                gender="M", phone="9000000011",
            )
        with tenant_scope(hospital_b):
            pb = Patient.objects.create(
                first_name="B1", last_name="Y", date_of_birth="1990-01-01",
                gender="F", phone="9000000012",
            )
        year = timezone.now().year
        assert pa.patient_id == f"PAT-{year}-0001"
        assert pb.patient_id == f"PAT-{year}-0001"   # same ID, different hospital - allowed
        assert pa.pk != pb.pk

    def test_sequence_continues_within_same_hospital(self, hospital_a):
        with tenant_scope(hospital_a):
            p1 = Patient.objects.create(
                first_name="One", last_name="X", date_of_birth="1990-01-01",
                gender="M", phone="9000000021",
            )
            p2 = Patient.objects.create(
                first_name="Two", last_name="X", date_of_birth="1990-01-01",
                gender="F", phone="9000000022",
            )
        year = timezone.now().year
        assert p1.patient_id == f"PAT-{year}-0001"
        assert p2.patient_id == f"PAT-{year}-0002"


class TestRbacStillWorks:
    def test_receptionist_blocked_from_staff_admin_page(self, client, setup_hospital):
        with tenant_scope(setup_hospital["hospital"]):
            client.force_login(setup_hospital["receptionist"])
            resp = client.get("/accounts/staff/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 403

    def test_admin_can_open_staff_page(self, client, setup_hospital):
        with tenant_scope(setup_hospital["hospital"]):
            client.force_login(setup_hospital["admin"])
            resp = client.get("/accounts/staff/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 200

    def test_doctor_blocked_from_registering_patient(self, client, setup_hospital):
        with tenant_scope(setup_hospital["hospital"]):
            client.force_login(setup_hospital["doctor_user"])
            resp = client.get("/patients/add/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 403

    def test_doctor_sees_only_own_appointments(self, client, setup_hospital):
        h = setup_hospital["hospital"]
        other_doc_user = User(username="doc-a2", hospital=h, role=User.Role.DOCTOR)
        other_doc_user.set_password("x"); other_doc_user.save()
        with tenant_scope(h):
            other_doc = Doctor.objects.create(user=other_doc_user, specialization="Cardio")
            Appointment.objects.create(
                patient=setup_hospital["patient"], doctor=other_doc,
                appointment_date=timezone.now().date(), appointment_time="10:00",
            )
            client.force_login(setup_hospital["doctor_user"])
            resp = client.get("/appointments/", HTTP_HOST="acme.testserver")
        assert resp.status_code == 200
        assert list(resp.context["appointments"]) == []


class TestAppointmentBooking:
    def test_receptionist_can_book_appointment(self, client, setup_hospital):
        h = setup_hospital["hospital"]
        with tenant_scope(h):
            client.force_login(setup_hospital["receptionist"])
            resp = client.post(
                "/appointments/add/",
                {
                    "patient": setup_hospital["patient"].pk,
                    "doctor": setup_hospital["doctor"].pk,
                    "appointment_date": timezone.now().date().isoformat(),
                    "appointment_time": "11:30",
                    "reason": "Fever",
                },
                HTTP_HOST="acme.testserver",
            )
        assert resp.status_code == 302, getattr(resp, "context", None) and resp.context["form"].errors
        with tenant_scope(h):
            appt = Appointment.objects.get()
        assert appt.status == Appointment.Status.SCHEDULED
        assert appt.hospital_id == h.pk          # tenant auto-assign hua
        assert appt.fee == setup_hospital["doctor"].consultation_fee
        # Phase 3: booking ke turant baad signal -> Celery (eager) -> risk row
        from ml_engine.models import AppointmentRisk

        with tenant_scope(h):
            risk = AppointmentRisk.objects.filter(appointment=appt).first()
        assert risk is not None, "HTTP booking pe risk score banna chahiye tha"
        assert risk.engine == AppointmentRisk.Engine.RULES

    def test_double_booking_same_slot_rejected(self, client, setup_hospital):
        h = setup_hospital["hospital"]
        payload = {
            "patient": setup_hospital["patient"].pk,
            "doctor": setup_hospital["doctor"].pk,
            "appointment_date": timezone.now().date().isoformat(),
            "appointment_time": "12:00",
            "reason": "Checkup",
        }
        with tenant_scope(h):
            client.force_login(setup_hospital["receptionist"])
            assert client.post("/appointments/add/", payload, HTTP_HOST="acme.testserver").status_code == 302
            second = client.post("/appointments/add/", payload, HTTP_HOST="acme.testserver")
        assert second.status_code == 200  # form error ke saath wapas
        assert "already booked" in second.content.decode()


class TestBillingFlow:
    def test_full_invoice_cycle_with_pdf(self, client, setup_hospital):
        h = setup_hospital["hospital"]
        today = timezone.now().date()
        with tenant_scope(h):
            appt = Appointment.objects.create(
                patient=setup_hospital["patient"], doctor=setup_hospital["doctor"],
                appointment_date=today, appointment_time="09:00", fee=500,
                status=Appointment.Status.COMPLETED,
            )
            client.force_login(setup_hospital["receptionist"])

            # step 1: patient choose karo
            step1 = client.get(
                f"/billing/create/?patient={setup_hospital['patient'].pk}",
                HTTP_HOST="acme.testserver",
            )
            assert step1.status_code == 302

            # step 2: unbilled appointment ko invoice banao
            create = client.post(
                f"/billing/create/{setup_hospital['patient'].pk}/",
                {"appointments": [appt.pk]},
                HTTP_HOST="acme.testserver",
            )
            assert create.status_code == 302, create.context and create.context.get("messages")

            invoice = Invoice.objects.get()
            assert invoice.hospital_id == h.pk
            assert invoice.invoice_number.startswith("INV-")
            assert invoice.total_amount == 500
            appt.refresh_from_db()
            assert appt.is_billed is True

            # payment record karo
            pay = client.post(
                f"/billing/{invoice.pk}/pay/",
                {"amount": "500", "mode": "UPI"},
                HTTP_HOST="acme.testserver",
            )
            assert pay.status_code == 302
            invoice.refresh_from_db()
            assert invoice.status == Invoice.Status.PAID
            assert Payment.objects.get().hospital_id == h.pk

            # PDF banta hai (ReportLab) - feature break nahi hua
            pdf = client.get(f"/billing/{invoice.pk}/pdf/", HTTP_HOST="acme.testserver")
        assert pdf.status_code == 200
        assert pdf["Content-Type"] == "application/pdf"
        assert pdf.content[:4] == b"%PDF"
