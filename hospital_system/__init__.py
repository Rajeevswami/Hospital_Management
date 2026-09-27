# Load the Celery app at Django startup, so @shared_task decorators are
# registered (otherwise the worker cannot find the tasks).
from .celery import app as celery_app

__all__ = ("celery_app",)
