"""
Ek hi entry point: `score_appointment(appointment)`.

Engine selection:
  1. Trained sklearn model (hospital-specific, warna global) - agar available hai
  2. Warna rule-based engine (cold start)
  3. Dono fail ho to None - appointment create/update KABHI block nahi hota

Yeh function tenant context maangta hai (AppointmentRisk tenant-scoped hai) -
Celery task aur signal isi wajah se `tenant_context()` use karte hain.
"""
import logging

from django.conf import settings

from . import rules
from .features import extract_for_appointment
from .models import AppointmentRisk
from .sklearn_model import get_predictor

logger = logging.getLogger(__name__)


def score_appointment(appointment):
    """
    Risk compute karke AppointmentRisk row upsert karta hai.
    Returns AppointmentRisk ya None.
    """
    try:
        features = extract_for_appointment(appointment)
    except Exception:
        logger.exception("Feature extraction fail: appointment %s", appointment.pk)
        return None

    engine = AppointmentRisk.Engine.RULES
    model_version = ""
    try:
        predictor = get_predictor(appointment.hospital)
        if predictor is not None:
            score = predictor.predict_proba(features)
            engine = AppointmentRisk.Engine.SKLEARN
            model_version = predictor.version
            reasons = [{
                "factor": "model",
                "impact": round(score, 3),
                "note": f"GradientBoosting model ({predictor.version}) se score",
            }]
        else:
            score, reasons = rules.score(features)
    except Exception:
        logger.exception("ML prediction fail - rule-based pe fall back")
        score, reasons = rules.score(features)
        engine = AppointmentRisk.Engine.RULES
        model_version = ""

    level = rules.classify(score)

    risk, _created = AppointmentRisk.objects.update_or_create(
        appointment=appointment,
        defaults={
            "score": score,
            "level": level,
            "engine": engine,
            "reasons": reasons,
            "model_version": model_version,
            "hospital": appointment.hospital,
        },
    )
    return risk


def readiness(hospital=None):
    """
    Kitna labelled data hai aur ML ke liye kaafi hai ya nahi.
    `manage.py no_show_census` aur preflight isi ko use karte hain.
    """
    from .features import OUTCOME_STATUSES
    from .sklearn_model import collect_training_data

    min_rows = getattr(settings, "ML_MIN_TRAINING_ROWS", 250)
    X, y = collect_training_data(hospital=hospital)
    labelled = len(X)
    positives = sum(y)
    return {
        "labelled_rows": labelled,
        "no_show_rows": positives,
        "positive_rate": round(positives / labelled, 4) if labelled else 0.0,
        "required_rows": min_rows,
        "ml_ready": labelled >= min_rows and 0 < positives < labelled,
        "verdict": (
            f"ML model train ho sakta hai ({labelled} labelled rows)"
            if labelled >= min_rows and 0 < positives < labelled
            else f"COLD START - rule-based engine chalega ({labelled}/{min_rows} labelled rows)"
        ),
    }
