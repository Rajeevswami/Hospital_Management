"""
Ek hi entry point: `score_appointment(appointment)`.

Engine selection:
  1. Trained sklearn model (hospital-specific, else global) - if available
  2. Otherwise the rule-based engine (cold start)
  3. If both fail -> None - appointment create/update is NEVER blocked

This function requires a tenant context (AppointmentRisk is tenant-scoped) -
that is why the Celery task and signal use `tenant_context()`.
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
    Computes the risk and upserts an AppointmentRisk row.
    Returns AppointmentRisk or None.
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
                "note": f"score from GradientBoosting model ({predictor.version})",
            }]
        else:
            score, reasons = rules.score(features)
    except Exception:
        logger.exception("ML prediction failed - falling back to rule-based")
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
    How much labelled data exists and whether it is enough for ML.
    `manage.py no_show_census` and preflight use this.
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
            f"ML model can be trained ({labelled} labelled rows)"
            if labelled >= min_rows and 0 < positives < labelled
            else f"COLD START - rule-based engine will run ({labelled}/{min_rows} labelled rows)"
        ),
    }
