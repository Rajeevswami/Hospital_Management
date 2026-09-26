"""
Celery tasks - appointment create hone pe async risk scoring.

Local dev mein `CELERY_TASK_ALWAYS_EAGER=True` rakho to task turant (sync) chalta
hai, broker ki zaroorat nahi. Production mein Redis broker chahiye.
"""
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="ml_engine.score_appointment", bind=True, max_retries=3,
             default_retry_delay=30)
def score_appointment_task(self, appointment_id):
    """
    Appointment ka no-show risk compute karo.

    Zaroori: Celery worker ke paas HTTP request (aur isliye middleware wala
    tenant context) NAHI hota. Isliye appointment se hospital nikal kar
    `tenant_context()` explicitly set karte hain - warna scoped managers
    ImproperlyConfigured denge.
    """
    from appointments.models import Appointment
    from tenants.context import tenant_context

    from .predict import score_appointment

    try:
        appointment = Appointment.all_objects.select_related(
            "patient", "doctor", "hospital"
        ).get(pk=appointment_id)
    except Appointment.DoesNotExist:
        logger.warning("Appointment %s nahi mila (delete ho gaya?)", appointment_id)
        return {"appointment_id": appointment_id, "scored": False, "reason": "not found"}

    with tenant_context(appointment.hospital):
        risk = score_appointment(appointment)

    if risk is None:
        return {"appointment_id": appointment_id, "scored": False, "reason": "scoring failed"}

    logger.info(
        "Appointment %s scored: %.3f (%s, engine=%s)",
        appointment_id, risk.score, risk.level, risk.engine,
    )
    return {
        "appointment_id": appointment_id,
        "scored": True,
        "score": risk.score,
        "level": risk.level,
        "engine": risk.engine,
        "scored_at": timezone.now().isoformat(),
    }
