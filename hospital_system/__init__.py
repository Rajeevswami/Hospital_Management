# Celery app ko Django startup pe hi load karo, taaki @shared_task decorators
# register ho jaayein (warna worker tasks dhundh nahi paata).
from .celery import app as celery_app

__all__ = ("celery_app",)
