"""
Triggers async risk scoring when an appointment is created.

Do safety rules:
  1. `dispatch_uid` - so the signal is not registered twice (on dev server reload)
  2. the whole handler is in try/except - appointment creation must NEVER
     fail because of ML/Celery
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
        # with ALWAYS_EAGER=True (local/tests) this runs immediately, otherwise it is queued
        score_appointment_task.delay(instance.pk)
    except Exception:
        # Whether the broker is down or anything else - appointment booking must not break
        logger.exception(
            "Could not enqueue risk scoring (appointment %s). "
            "You can backfill later with `manage.py score_appointments`.",
            instance.pk,
        )
