"""
Feature extraction - BOTH the rules engine and the ML model use this,
so training and prediction cannot have a feature mismatch (a silent accuracy killer).

Important rule: only information that was available at the moment the
appointment was CREATED is used. Future data (like the appointment's final
status) never becomes a feature - otherwise the model looks perfect in
training and is useless in production.
"""
from datetime import date

from django.utils import timezone

# Order matters - the model requests features in this order.
FEATURE_NAMES = [
    "lead_days",              # how many days from booking to the appointment
    "lead_hours",             # the same, in hours (to catch same-day bookings)
    "is_same_day",            # booked today and coming today
    "day_of_week",            # 0=Mon .. 6=Sun
    "is_weekend",
    "is_monday",              # no-shows are more common on Mondays (weekend backlog)
    "hour_of_day",
    "is_early_slot",          # before 9 AM
    "is_late_slot",           # after 5 PM
    "patient_age",
    "patient_prev_appts",     # how many appointments this patient had before
    "patient_prev_no_shows",
    "patient_no_show_rate",
    "days_since_last_visit",
    "is_first_visit",
    "doctor_prev_appts",
    "doctor_no_show_rate",
    "fee",
    "has_reason",
    "has_emergency_contact",
    "has_phone",
]

# The final no-show label
NO_SHOW_STATUS = "NO_SHOW"
OUTCOME_STATUSES = ("COMPLETED", "NO_SHOW", "CANCELLED")


def _days_between(a, b):
    if not a or not b:
        return 0
    return (b - a).days


def extract_for_appointment(appointment):
    """
    Feature dict for one appointment.
    The related patient/doctor queries require a tenant context -
    so always call this function inside a request or `tenant_context()`.
    """
    now = timezone.now()
    appt_dt = timezone.datetime.combine(
        appointment.appointment_date, appointment.appointment_time
    )
    if timezone.is_aware(now):
        appt_dt = timezone.make_aware(appt_dt, timezone.get_current_timezone())

    created = appointment.created_at or now
    lead = appt_dt - created
    lead_hours = max(lead.total_seconds() / 3600.0, 0.0)
    lead_days = lead_hours / 24.0

    patient = appointment.patient
    doctor = appointment.doctor

    # ---- patient history (only records from BEFORE this appointment) ----
    prev_appts = _patient_history(patient, appointment)

    prev_count = prev_appts["count"]
    prev_no_shows = prev_appts["no_shows"]
    no_show_rate = (prev_no_shows / prev_count) if prev_count else 0.0
    last_visit = prev_appts["last_visit"]

    # ---- doctor history ----
    doc = _doctor_history(doctor, appointment)

    return {
        "lead_days": round(lead_days, 4),
        "lead_hours": round(lead_hours, 2),
        "is_same_day": int(lead_days < 1),
        "day_of_week": appointment.appointment_date.weekday(),
        "is_weekend": int(appointment.appointment_date.weekday() >= 5),
        "is_monday": int(appointment.appointment_date.weekday() == 0),
        "hour_of_day": appointment.appointment_time.hour,
        "is_early_slot": int(appointment.appointment_time.hour < 9),
        "is_late_slot": int(appointment.appointment_time.hour >= 17),
        "patient_age": _age(patient.date_of_birth, appointment.appointment_date),
        "patient_prev_appts": prev_count,
        "patient_prev_no_shows": prev_no_shows,
        "patient_no_show_rate": round(no_show_rate, 4),
        "days_since_last_visit": _days_between(last_visit, appointment.appointment_date) if last_visit else -1,
        "is_first_visit": int(prev_count == 0),
        "doctor_prev_appts": doc["count"],
        "doctor_no_show_rate": round(doc["no_shows"] / doc["count"], 4) if doc["count"] else 0.0,
        "fee": float(appointment.fee or 0),
        "has_reason": int(bool((appointment.reason or "").strip())),
        "has_emergency_contact": int(bool((patient.emergency_contact_phone or "").strip())),
        "has_phone": int(bool((patient.phone or "").strip())),
    }


def to_vector(features):
    """dict -> list, in FEATURE_NAMES order."""
    return [float(features[name]) for name in FEATURE_NAMES]


def _age(dob, on_date):
    if not dob:
        return 0
    on_date = on_date or date.today()
    return on_date.year - dob.year - ((on_date.month, on_date.day) < (dob.month, dob.day))


def _patient_history(patient, appointment):
    """Tally of the patient's appointments before this appointment."""
    from appointments.models import Appointment

    qs = Appointment.all_objects.filter(
        patient_id=patient.pk,
        hospital_id=patient.hospital_id,
        status__in=OUTCOME_STATUSES,
    ).exclude(pk=appointment.pk).filter(
        appointment_date__lte=appointment.appointment_date
    )
    rows = list(qs.values("status", "appointment_date").order_by("-appointment_date"))
    last_visit = rows[0]["appointment_date"] if rows else None
    return {
        "count": len(rows),
        "no_shows": sum(1 for r in rows if r["status"] == NO_SHOW_STATUS),
        "last_visit": last_visit,
    }


def _doctor_history(doctor, appointment):
    from appointments.models import Appointment

    if doctor is None:
        return {"count": 0, "no_shows": 0}
    qs = Appointment.all_objects.filter(
        doctor_id=doctor.pk,
        hospital_id=doctor.hospital_id,
        status__in=OUTCOME_STATUSES,
        appointment_date__lt=appointment.appointment_date,
    )
    rows = list(qs.values("status"))
    return {
        "count": len(rows),
        "no_shows": sum(1 for r in rows if r["status"] == NO_SHOW_STATUS),
    }
