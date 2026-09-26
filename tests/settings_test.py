"""
Test-only settings override.

WhiteNoise ka CompressedManifestStaticFilesStorage `collectstatic` ke bina
"Missing staticfiles manifest entry" error deta hai (templates {% static %} use
karte hain), isliye tests mein simple storage backend.
"""
from hospital_system.settings import *  # noqa: F401,F403

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# ---- Phase 3: ML / Celery ----
# Broker ke bina task turant chale (sync) - warna tests ko Redis chahiye hota.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# Trained models repo ke andar na banein - pytest ke temp dir mein
import tempfile  # noqa: E402

ML_MODEL_DIR = tempfile.mkdtemp(prefix="ml_models_test_")
# Chhota threshold taaki training pipeline ko kam synthetic data se test kar saken
ML_MIN_TRAINING_ROWS = 40

