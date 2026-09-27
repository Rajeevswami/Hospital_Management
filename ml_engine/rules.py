"""
Cold-start (rule-based) no-show risk score.

This engine runs when labelled data is below `ML_MIN_TRAINING_ROWS`.
Design principles:
  * a score between 0.0 - 1.0, starting from base 0.20
  * each factor's impact is recorded separately -> the UI can show "why high
    risk" (it is not a black box)
  * the weights are deliberately conservative: being confident without data is wrong

The weights come from intuition (industry literature + common sense); they are
not fitted to any dataset. Once data is available, the sklearn engine replaces this one.
"""
from django.conf import settings

BASE_SCORE = 0.20

# (factor, weight, direction) - all weights are positive, the direction logic is separate
LEAD_TIME_BANDS = [
    # (max_days, impact, note)
    (0.25, -0.06, "Booked today - more likely to come"),
    (1.0, 0.00, "Appointment tomorrow"),
    (3.0, 0.04, "Appointment in 2-3 days"),
    (7.0, 0.09, "Booked more than a week ahead"),
    (30.0, 0.14, "Booked very far ahead - easy to forget"),
    (10 ** 9, 0.18, "Booked more than a month ago"),
]


def score(features):
    """
    Returns (score: float 0-1, reasons: list[dict])
    `features` is the dict from ml_engine.features.extract_for_appointment().
    """
    reasons = []
    total = BASE_SCORE

    def add(factor, impact, note):
        nonlocal total
        if impact == 0:
            return
        total += impact
        reasons.append({
            "factor": factor,
            "impact": round(impact, 3),
            "note": note,
        })

    # ---------- 1. lead time (sabse strong signal) ----------
    lead_days = features.get("lead_days", 0)
    for max_days, impact, note in LEAD_TIME_BANDS:
        if lead_days < max_days:
            add("lead_time", impact, f"{note} ({lead_days:.1f} days)")
            break

    # ---------- 2. day-of-week pattern ----------
    if features.get("is_monday"):
        add("day_of_week", 0.05, "Monday appointment (weekend backlog)")
    elif features.get("is_weekend"):
        add("day_of_week", 0.03, "Weekend appointment")
    elif features.get("day_of_week") == 4:
        add("day_of_week", 0.02, "Friday appointment")

    # ---------- 3. time slot ----------
    if features.get("is_early_slot"):
        add("slot", 0.04, "Early morning slot (before 9)")
    elif features.get("is_late_slot"):
        add("slot", 0.03, "Evening slot (after 5)")

    # ---------- 4. the patient's track record (most reliable, when data exists) ----------
    prev = features.get("patient_prev_appts", 0)
    no_shows = features.get("patient_prev_no_shows", 0)
    if prev >= 2:
        rate = no_shows / prev
        if rate >= 0.5:
            add("patient_history", 0.22, f"Patient did not come {no_shows}/{prev} times before")
        elif rate >= 0.25:
            add("patient_history", 0.12, f"Patient did not come {no_shows}/{prev} times before")
        elif no_shows == 0 and prev >= 3:
            add("patient_history", -0.08, f"Patient came {prev} times and never missed")
    elif prev == 1 and no_shows == 1:
        add("patient_history", 0.10, "Patient has already missed their previous appointment")

    if features.get("is_first_visit"):
        add("first_visit", 0.05, "First appointment - no familiarity with the hospital")

    # ---------- 5. the doctor's pattern ----------
    doc_prev = features.get("doctor_prev_appts", 0)
    if doc_prev >= 10 and features.get("doctor_no_show_rate", 0) >= 0.2:
        add("doctor_pattern", 0.06,
            f"{features['doctor_no_show_rate']:.0%} no-show rate among this doctor's appointments")

    # ---------- 6. contact quality (can a reminder reach them or not) ----------
    if not features.get("has_phone"):
        add("contact", 0.10, "Patient has no phone number - no reminder can be sent")
    elif not features.get("has_emergency_contact"):
        add("contact", 0.02, "Emergency contact not filled in")

    # ---------- 7. reason / fee ----------
    if not features.get("has_reason"):
        add("reason", 0.03, "Visit reason not written")
    if features.get("fee", 0) == 0:
        add("fee", 0.04, "Free consultation (no-commitment booking)")

    # ---------- clamp ----------
    final = max(0.01, min(0.99, total))
    reasons.sort(key=lambda r: -abs(r["impact"]))
    return round(final, 4), reasons


def classify(score):
    """Score -> LOW / MEDIUM / HIGH"""
    from ml_engine.models import AppointmentRisk

    high = getattr(settings, "ML_HIGH_RISK_THRESHOLD", 0.65)
    medium = getattr(settings, "ML_MEDIUM_RISK_THRESHOLD", 0.40)
    if score >= high:
        return AppointmentRisk.Level.HIGH
    if score >= medium:
        return AppointmentRisk.Level.MEDIUM
    return AppointmentRisk.Level.LOW
