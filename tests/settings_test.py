"""
Test-only settings override.

WhiteNoise's CompressedManifestStaticFilesStorage raises "Missing staticfiles
manifest entry" without `collectstatic` (the templates use {% static %}), so the
tests use a simple storage backend.
"""
from hospital_system.settings import *  # noqa: F401,F403

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# ---- Phase 3: ML / Celery ----
# Tasks run immediately (sync) without a broker - otherwise the tests would need Redis.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# Trained models must not be created inside the repo - in pytest's temp dir
import tempfile  # noqa: E402

ML_MODEL_DIR = tempfile.mkdtemp(prefix="ml_models_test_")
# Small threshold so the training pipeline can be tested with little synthetic data
ML_MIN_TRAINING_ROWS = 40

