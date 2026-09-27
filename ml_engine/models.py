"""
Phase 3 - persisted results of the no-show prediction.

Do models:
  AppointmentRisk     - every appointment's risk score (tenant-scoped, OneToOne)
  NoShowModelArtifact - metadata of a trained ML model (the actual joblib file is on disk)

Why not put the risk score directly on Appointment?
  * no migration churn in the core table
  * an appointment's score can be recomputed multiple times (model retrain) -
    keeping history/engine/version together in a separate table is cleaner
"""
from django.db import models

from tenants.models import TenantModel


class AppointmentRisk(TenantModel):
    class Level(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    class Engine(models.TextChoices):
        RULES = "rules", "Rule-based (cold start)"
        SKLEARN = "sklearn", "GradientBoosting"

    appointment = models.OneToOneField(
        "appointments.Appointment", on_delete=models.CASCADE, related_name="risk",
    )
    score = models.FloatField(help_text="0.0 - 1.0; the higher, the more likely a no-show")
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.LOW, db_index=True)
    engine = models.CharField(max_length=10, choices=Engine.choices, default=Engine.RULES)
    reasons = models.JSONField(
        default=list, blank=True,
        help_text='Human-readable reasons: [{"factor": "lead_time", "impact": 0.12, "note": "..."}]',
    )
    model_version = models.CharField(max_length=60, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score"]
        indexes = [models.Index(fields=["level", "-score"])]

    def __str__(self):
        return f"Appointment #{self.appointment_id} risk={self.score:.2f} ({self.level})"

    @property
    def score_pct(self):
        return round(self.score * 100, 1)

    @classmethod
    def high_risk_upcoming(cls, limit=8):
        """
        For the dashboard alert: upcoming HIGH-risk appointments.
        objects is tenant-scoped, so it automatically returns only this
        hospital's data.
        """
        from django.utils import timezone

        from appointments.models import Appointment

        return (
            cls.objects.filter(
                level=cls.Level.HIGH,
                appointment__appointment_date__gte=timezone.now().date(),
                appointment__status=Appointment.Status.SCHEDULED,
            )
            .select_related("appointment__patient", "appointment__doctor__user")
            .order_by("-score", "appointment__appointment_date")[:limit]
        )


class NoShowModelArtifact(models.Model):
    """
    Record of a trained model. The binary joblib file lives in ML_MODEL_DIR -
    only metadata + relative path in the DB.

    NOTE: Render's filesystem is EPHEMERAL. Artifacts vanish on deploy, so
    either run `train_no_show` after deploy (cron/release command), or keep the
    artifacts in object storage (S3/GCS). This limitation is documented.
    """

    version = models.CharField(max_length=60, unique=True, db_index=True)
    hospital = models.ForeignKey(
        "tenants.Hospital", on_delete=models.CASCADE, null=True, blank=True,
        related_name="no_show_models",
        help_text="NULL = trained on all hospitals' combined data (global model)",
    )
    algorithm = models.CharField(max_length=60, default="GradientBoostingClassifier")
    relative_path = models.CharField(max_length=255)

    training_rows = models.PositiveIntegerField(default=0)
    positive_rate = models.FloatField(default=0, help_text="The no-show share of the training set")
    metrics = models.JSONField(default=dict, blank=True)
    feature_names = models.JSONField(default=list, blank=True)
    feature_importances = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True, help_text="Use this version for predictions")
    trained_at = models.DateTimeField(auto_now_add=True)
    trained_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-trained_at"]

    def __str__(self):
        scope = self.hospital.slug if self.hospital_id else "global"
        return f"{self.version} ({scope}, {self.training_rows} rows)"

    @classmethod
    def active_for(cls, hospital):
        """The hospital-specific model if found, else the global one, else None."""
        # NOTE: `all_objects` (unscoped) is used here deliberately -
        # the global model's hospital is NULL, so a tenant-scoped filter could
        # never find it. The hospital-specific lookup uses an explicit filter,
        # so there is no cross-tenant leak.
        if hospital is not None:
            specific = cls.all_objects.filter(
                hospital=hospital, is_active=True
            ).order_by("-trained_at").first()
            if specific:
                return specific
        return cls.all_objects.filter(
            hospital__isnull=True, is_active=True
        ).order_by("-trained_at").first()

    objects = models.Manager()          # default manager
    # This model is NOT tenant-scoped (the global model's hospital is NULL),
    # so a plain unscoped manager is kept here too - both are the same.
    all_objects = models.Manager()
