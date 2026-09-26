"""
Phase 3 - No-show prediction ke persisted results.

Do models:
  AppointmentRisk     - har appointment ka risk score (tenant-scoped, OneToOne)
  NoShowModelArtifact - train hue ML model ka metadata (actual joblib file disk pe)

Risk score Appointment pe directly kyun nahi?
  * core table mein migration churn nahi
  * ek appointment ka score kai baar recompute ho sakta hai (model retrain) -
    alag table mein history/engine/version saath rakhna saaf rehta hai
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
    score = models.FloatField(help_text="0.0 - 1.0; jitna zyada, no-show ka chance utna zyada")
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.LOW, db_index=True)
    engine = models.CharField(max_length=10, choices=Engine.choices, default=Engine.RULES)
    reasons = models.JSONField(
        default=list, blank=True,
        help_text='Human-readable wajah: [{"factor": "lead_time", "impact": 0.12, "note": "..."}]',
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
        Dashboard alert ke liye: aane wale HIGH risk appointments.
        objects tenant-scoped hai, isliye yeh automatically sirf isi hospital ka
        data deta hai.
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
    Train hue model ka record. Binary joblib file ML_MODEL_DIR mein rehti hai -
    DB mein sirf metadata + relative path.

    NOTE: Render ka filesystem EPHEMERAL hai. Deploy pe artifacts udd jaate hain,
    isliye ya to deploy ke baad `train_no_show` chalao (cron/release command), ya
    artifacts ko object storage (S3/GCS) mein rakho. Yeh limitation docs mein hai.
    """

    version = models.CharField(max_length=60, unique=True, db_index=True)
    hospital = models.ForeignKey(
        "tenants.Hospital", on_delete=models.CASCADE, null=True, blank=True,
        related_name="no_show_models",
        help_text="NULL = sab hospitals ka data mila ke train hua (global model)",
    )
    algorithm = models.CharField(max_length=60, default="GradientBoostingClassifier")
    relative_path = models.CharField(max_length=255)

    training_rows = models.PositiveIntegerField(default=0)
    positive_rate = models.FloatField(default=0, help_text="Training set mein no-show ka hissa")
    metrics = models.JSONField(default=dict, blank=True)
    feature_names = models.JSONField(default=list, blank=True)
    feature_importances = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True, help_text="Prediction ke liye yahi version use ho")
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
        """Hospital-specific model mile to wahi, warna global, warna None."""
        # NOTE: yahan deliberately `all_objects` (unscoped) use hota hai -
        # global model ka hospital NULL hota hai, tenant-scoped filter use
        # kabhi pakad hi nahi paata. Hospital-specific lookup explicit filter se
        # hota hai, isliye cross-tenant leak nahi hota.
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
    # Yeh model tenant-scoped NAHI hai (global model ka hospital NULL hota hai),
    # isliye yahan ek plain unscoped manager bhi rakha hai - dono same hain.
    all_objects = models.Manager()
