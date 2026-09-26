"""
scikit-learn GradientBoostingClassifier + joblib persistence.

Kab use hota hai: jab hospital ke paas `ML_MIN_TRAINING_ROWS` (default 250) se
zyada LABELLED appointments hon (status COMPLETED / NO_SHOW / CANCELLED).
Uske pehle ml_engine.rules wala rule-based engine chalta hai.

Fail-safe: model file missing / corrupt / sklearn installed nahi / feature list
mismatch -> exception phenkne ke bajaye None return karta hai, aur caller
rule-based engine pe fall back kar jaata hai. Prediction kabhi crash nahi karti.
"""
import logging
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .features import FEATURE_NAMES, NO_SHOW_STATUS, OUTCOME_STATUSES, extract_for_appointment, to_vector

logger = logging.getLogger(__name__)

ALGORITHM = "GradientBoostingClassifier"


def model_dir():
    return Path(getattr(settings, "ML_MODEL_DIR", "ml_models"))


def _now_tag():
    """
    Timestamp + short random suffix.
    Sirf timestamp kaafi nahi: do trainings same second mein ho jaayein
    (cron + manual, ya do hospitals) to UNIQUE(version) toot jaata hai.
    """
    import uuid

    return f"{timezone.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


class NoShowPredictor:
    """Trained model ka wrapper - save/load/predict."""

    def __init__(self, model, version, feature_names, training_rows=0, metrics=None):
        self.model = model
        self.version = version
        self.feature_names = feature_names
        self.training_rows = training_rows
        self.metrics = metrics or {}

    # ---------------- persistence ----------------
    def save(self, hospital=None):
        import joblib

        scope = hospital.slug if hospital is not None else "global"
        directory = model_dir() / scope
        directory.mkdir(parents=True, exist_ok=True)
        relative = f"{scope}/{self.version}.joblib"
        joblib.dump(
            {
                "model": self.model,
                "version": self.version,
                "feature_names": self.feature_names,
                "training_rows": self.training_rows,
                "metrics": self.metrics,
                "algorithm": ALGORITHM,
            },
            model_dir() / relative,
        )
        return relative

    @classmethod
    def load(cls, relative_path):
        import joblib

        path = model_dir() / relative_path
        if not path.exists():
            logger.warning("Model file nahi mili: %s", path)
            return None
        try:
            blob = joblib.load(path)
        except Exception:
            logger.exception("Model load nahi ho paya: %s", path)
            return None
        if blob.get("feature_names") != FEATURE_NAMES:
            logger.error(
                "Feature mismatch - model purane feature set pe train hua tha. Retrain karo."
            )
            return None
        return cls(
            model=blob["model"],
            version=blob.get("version", "unknown"),
            feature_names=blob["feature_names"],
            training_rows=blob.get("training_rows", 0),
            metrics=blob.get("metrics", {}),
        )

    # ---------------- prediction ----------------
    def predict_proba(self, features):
        if set(self.feature_names) != set(FEATURE_NAMES):
            raise ValueError("Model ke features current feature set se match nahi karte.")
        return float(self.model.predict_proba([to_vector(features)])[0][1])


# ------------------------------------------------------------------- training
def collect_training_data(hospital=None, limit=None):
    """
    Labelled appointments se (X, y) banao.
    hospital=None -> sab hospitals ka data (global model).
    """
    from appointments.models import Appointment

    qs = Appointment.all_objects.filter(status__in=OUTCOME_STATUSES).select_related(
        "patient", "doctor"
    )
    if hospital is not None:
        qs = qs.filter(hospital=hospital)
    qs = qs.order_by("appointment_date")
    if limit:
        qs = qs[:limit]

    X, y = [], []
    for appt in qs:
        try:
            feats = extract_for_appointment(appt)
        except Exception:
            logger.exception("Feature extraction fail: appointment %s", appt.pk)
            continue
        X.append(to_vector(feats))
        y.append(1 if appt.status == NO_SHOW_STATUS else 0)
    return X, y


def train(hospital=None, min_rows=None, random_state=42, trained_by=None):
    """
    Model train karo, joblib save karo, NoShowModelArtifact record banao.

    Returns dict: {"ok": bool, "reason"/"artifact"/"metrics": ...}
    Data kam ho to ok=False + reason - caller ko pata chalta hai ki rule-based
    engine hi chalta rahega.
    """
    min_rows = min_rows or getattr(settings, "ML_MIN_TRAINING_ROWS", 250)

    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.metrics import precision_score, recall_score, roc_auc_score
        from sklearn.model_selection import train_test_split
    except ImportError as exc:
        return {"ok": False, "reason": f"scikit-learn installed nahi hai: {exc}"}

    X, y = collect_training_data(hospital=hospital)
    total = len(X)
    if total < min_rows:
        return {
            "ok": False,
            "reason": (
                f"Sirf {total} labelled appointments hain, {min_rows} chahiye. "
                "Tab tak rule-based engine chalega (yeh expected hai)."
            ),
            "rows": total,
            "required": min_rows,
        }

    positives = sum(y)
    if positives == 0 or positives == total:
        return {
            "ok": False,
            "reason": f"Labels ek-tarfa hain ({positives}/{total} no-show) - model train nahi ho sakta.",
            "rows": total,
        }

    # Stratified split taaki chhote class ka distribution dono side same rahe
    stratify = y if 0 < positives < total and min(positives, total - positives) >= 2 else None
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=random_state, stratify=stratify
    )

    clf = GradientBoostingClassifier(
        n_estimators=120, learning_rate=0.08, max_depth=3, random_state=random_state
    )
    clf.fit(X_train, y_train)

    metrics = {"training_rows": total, "positive_rate": round(positives / total, 4)}
    try:
        proba = clf.predict_proba(X_test)[:, 1]
        metrics["roc_auc"] = round(float(roc_auc_score(y_test, proba)), 4)
        metrics["precision"] = round(float(precision_score(y_test, proba >= 0.5, zero_division=0)), 4)
        metrics["recall"] = round(float(recall_score(y_test, proba >= 0.5, zero_division=0)), 4)
        metrics["baseline_no_show_rate"] = round(sum(y_test) / len(y_test), 4)
    except Exception:
        logger.exception("Metrics compute nahi ho paye (model phir bhi save hoga)")

    version = f"gbm_{hospital.slug + '_' if hospital else ''}{_now_tag()}"
    predictor = NoShowPredictor(
        model=clf, version=version, feature_names=list(FEATURE_NAMES),
        training_rows=total, metrics=metrics,
    )
    relative_path = predictor.save(hospital=hospital)

    from .models import NoShowModelArtifact

    # Purane models ko deactivate + unki files delete (model dir bada na ho).
    # Rollback chahiye to admin se kisi purane artifact ko is_active kar do -
    # bas uski file retrain se pehle delete na ho, isliye yeh cleanup opt-in hai.
    previous = list(
        NoShowModelArtifact.all_objects.filter(hospital=hospital, is_active=True)
    )
    NoShowModelArtifact.all_objects.filter(
        hospital=hospital, is_active=True
    ).update(is_active=False)
    for old_artifact in previous:
        try:
            (model_dir() / old_artifact.relative_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Purani model file delete nahi ho payi: %s", old_artifact.relative_path)

    artifact = NoShowModelArtifact.objects.create(
        version=version,
        hospital=hospital,
        algorithm=ALGORITHM,
        relative_path=relative_path,
        training_rows=total,
        positive_rate=metrics["positive_rate"],
        metrics=metrics,
        feature_names=list(FEATURE_NAMES),
        feature_importances={
            name: round(float(imp), 4)
            for name, imp in zip(FEATURE_NAMES, clf.feature_importances_)
        },
        is_active=True,
        trained_by=trained_by,
    )
    return {"ok": True, "artifact": artifact, "metrics": metrics, "version": version}


def get_predictor(hospital=None):
    """Active trained model, ya None (-> caller rule-based use karega)."""
    from .models import NoShowModelArtifact

    artifact = NoShowModelArtifact.active_for(hospital)
    if artifact is None:
        return None
    predictor = NoShowPredictor.load(artifact.relative_path)
    if predictor is not None:
        predictor.version = artifact.version
    return predictor
