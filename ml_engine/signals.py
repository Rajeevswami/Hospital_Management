"""
Appointment create hone pe async risk scoring trigger.

Do safety rules:
  1. `dispatch_uid` - signal dobara register na ho (dev server reload pe)
  2. poora handler try/except mein - ML/Celery ki wajah se appointment create
     KABHI fail nahi hona chahiye
"""
import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender="appointments.Appointment",
          dispatch_uid="ml_engine_score_on_appointment_create")
def score_on_appointment_create(sender, instance, created, **kwargs):
    if not created:
        return
    if not getattr(settings, "ML_AUTO_SCORE_ON_CREATE", True):
        return

    from .tasks import score_appointment_task

    try:
        # ALWAYS_EAGER=True (local/tests) par yeh turant chalta hai, warna queue mein
        score_appointment_task.delay(instance.pk)
    except Exception:
        # Broker down ho ya kuch bhi - appointment booking break nahi honi chahiye
        logger.exception(
            "Risk scoring enqueue nahi ho paya (appointment %s). "
            "`manage.py score_appointments` se baad mein kar sakte ho.",
            instance.pk,
        )
