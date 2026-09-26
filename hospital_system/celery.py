"""
Celery application (Phase 3).

Broker: Redis (REDIS_URL / CELERY_BROKER_URL se). Local dev mein
`CELERY_TASK_ALWAYS_EAGER=True` rakho to broker ke bina tasks sync chalte hain.

Worker chalane ka command:
    celery -A hospital_system worker -l info
Beat (scheduled tasks, Phase 5+):
    celery -A hospital_system beat -l info
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hospital_system.settings")

app = Celery("hospital_system")
# settings.py ke CELERY_* variables yahan aa jaate hain (namespace='CELERY')
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, name="hospital_system.debug_ping")
def debug_ping(self):
    """Broker/worker connectivity check: celery -A hospital_system call hospital_system.debug_ping"""
    return {"pong": True}
