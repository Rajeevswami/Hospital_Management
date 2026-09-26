"""
Cold-start (rule-based) no-show risk score.

Jab labelled data `ML_MIN_TRAINING_ROWS` se kam ho, tab yeh engine chalta hai.
Design principles:
  * 0.0 - 1.0 ke beech score, base 0.20 se shuru
  * har factor ka impact separately record hota hai -> UI pe "kyun high risk"
    dikha sakte hain (black-box nahi)
  * weights deliberately conservative hain: bina data ke confident hona galat hai

Weights intuition se aaye hain (industry literature + common sense), kisi dataset
se fit nahi kiye gaye. Jab data mil jaaye to sklearn engine replace kar deta hai.
"""
from django.conf import settings

BASE_SCORE = 0.20

# (factor, weight, direction) - sab weights positive, direction logic alag hai
LEAD_TIME_BANDS = [
    # (max_days, impact, note)
    (0.25, -0.06, "Aaj hi book hua - aane ka chance zyada"),
    (1.0, 0.00, "Kal ka appointment"),
    (3.0, 0.04, "2-3 din baad ka appointment"),
    (7.0, 0.09, "Ek hafte se zyada pehle book hua"),
    (30.0, 0.14, "Bahut pehle book hua - bhool jaane ka chance"),
    (10 ** 9, 0.18, "Ek mahine se zyada purani booking"),
]


def score(features):
    """
    Returns (score: float 0-1, reasons: list[dict])
    `features` ml_engine.features.extract_for_appointment() se aaya dict hai.
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
            add("lead_time", impact, f"{note} ({lead_days:.1f} din)")
            break

    # ---------- 2. din ka pattern ----------
    if features.get("is_monday"):
        add("day_of_week", 0.05, "Monday ka appointment (weekend backlog)")
    elif features.get("is_weekend"):
        add("day_of_week", 0.03, "Weekend ka appointment")
    elif features.get("day_of_week") == 4:
        add("day_of_week", 0.02, "Friday ka appointment")

    # ---------- 3. time slot ----------
    if features.get("is_early_slot"):
        add("slot", 0.04, "Subah jaldi ka slot (9 se pehle)")
    elif features.get("is_late_slot"):
        add("slot", 0.03, "Shaam ka slot (5 ke baad)")

    # ---------- 4. patient ka track record (sabse reliable, jab data ho) ----------
    prev = features.get("patient_prev_appts", 0)
    no_shows = features.get("patient_prev_no_shows", 0)
    if prev >= 2:
        rate = no_shows / prev
        if rate >= 0.5:
            add("patient_history", 0.22, f"Patient pehle {no_shows}/{prev} baar nahi aaya")
        elif rate >= 0.25:
            add("patient_history", 0.12, f"Patient pehle {no_shows}/{prev} baar nahi aaya")
        elif no_shows == 0 and prev >= 3:
            add("patient_history", -0.08, f"Patient {prev} baar aaya hai, kabhi miss nahi kiya")
    elif prev == 1 and no_shows == 1:
        add("patient_history", 0.10, "Patient apna pichla appointment miss kar chuka hai")

    if features.get("is_first_visit"):
        add("first_visit", 0.05, "Pehla appointment - hospital se familiarity nahi")

    # ---------- 5. doctor ka pattern ----------
    doc_prev = features.get("doctor_prev_appts", 0)
    if doc_prev >= 10 and features.get("doctor_no_show_rate", 0) >= 0.2:
        add("doctor_pattern", 0.06,
            f"Is doctor ke appointments mein {features['doctor_no_show_rate']:.0%} no-show rate")

    # ---------- 6. contact quality (reminder pahunch payega ya nahi) ----------
    if not features.get("has_phone"):
        add("contact", 0.10, "Patient ka phone number nahi hai - reminder nahi jaayega")
    elif not features.get("has_emergency_contact"):
        add("contact", 0.02, "Emergency contact nahi bhara hua")

    # ---------- 7. reason / fee ----------
    if not features.get("has_reason"):
        add("reason", 0.03, "Visit ka reason nahi likha")
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
