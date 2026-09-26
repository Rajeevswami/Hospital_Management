"""
Phase 2 - SaaS subscription & plan gating.

Do models:
  Plan         - platform-level catalog (tenant-scoped NAHI; saas operator manage karta hai)
  Subscription - ek hospital ka current plan (tenant-scoped, OneToOne)

Design note: patient-facing `billing` app (Invoice/Payment) se yeh ALAG hai -
yahan hospital khud customer hai, patient nahi.
"""
import logging

from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


class Feature(models.TextChoices):
    """
    Gate karne layak cheezein. Naya feature add karna ho to yahan enum add karo
    aur har plan ke features_json mein daalo - gating code change nahi karna padega.
    """

    AI_NO_SHOW = "ai_no_show", "AI no-show prediction"
    API_ACCESS = "api_access", "REST API access"
    SMS_REMINDERS = "sms_reminders", "SMS appointment reminders"
    CUSTOM_BRANDING = "custom_branding", "Custom branding / logo"
    MULTI_BRANCH = "multi_branch", "Multi-branch support"
    ADVANCED_REPORTS = "advanced_reports", "Advanced reports & exports"


# Sab features ki default value - plan ke features_json mein na ho to yeh use hoga
DEFAULT_FEATURE_FLAGS = {
    Feature.AI_NO_SHOW: False,
    Feature.API_ACCESS: False,
    Feature.SMS_REMINDERS: False,
    Feature.CUSTOM_BRANDING: False,
    Feature.MULTI_BRANCH: False,
    Feature.ADVANCED_REPORTS: False,
}

# patient_limit / staff_limit ke liye "unlimited" sentinel
UNLIMITED = -1


class PlanManager(models.Manager):
    def active(self):
        return self.get_queryset().filter(is_active=True)


class Plan(models.Model):
    """
    Platform catalog. Tenant-scoped nahi hai - warna naya tenant apna plan hi
    na dekh paata. Access control admin + views se hota hai.
    """

    class Interval(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    name = models.CharField(max_length=80)
    code = models.SlugField(max_length=40, unique=True, help_text="e.g. starter, growth")
    description = models.CharField(max_length=255, blank=True)

    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Per billing period, INR mein (Razorpay ko paisa mein bhejte hain)",
    )
    currency = models.CharField(max_length=3, default="INR")
    interval = models.CharField(max_length=10, choices=Interval.choices, default=Interval.MONTHLY)

    # ---------------- limits ----------------
    patient_limit = models.IntegerField(
        default=100, help_text=f"Isse zyada patients nahi. {UNLIMITED} = unlimited"
    )
    staff_limit = models.IntegerField(
        default=5, help_text=f"Kitne staff logins. {UNLIMITED} = unlimited"
    )
    appointment_limit = models.IntegerField(
        default=UNLIMITED, help_text=f"Monthly appointments. {UNLIMITED} = unlimited"
    )

    # ---------------- feature flags ----------------
    features_json = models.JSONField(
        default=dict, blank=True,
        help_text='{"ai_no_show": true, "api_access": false, ...}. Jo key missing '
                  "ho uski default False maani jaati hai.",
    )

    # ---------------- Razorpay ----------------
    razorpay_plan_id = models.CharField(
        max_length=60, blank=True,
        help_text="Razorpay pe bana hua plan id (plan_xxx). Recurring checkout ke liye zaroori.",
    )
    trial_days = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True, help_text="False = naye subscriptions band")
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PlanManager()

    class Meta:
        ordering = ["sort_order", "price"]

    def __str__(self):
        return f"{self.name} (Rs.{self.price}/{self.interval})"

    # ---------------- feature helpers ----------------
    @property
    def features(self):
        """features_json + defaults merge karke."""
        merged = dict(DEFAULT_FEATURE_FLAGS)
        if isinstance(self.features_json, dict):
            merged.update({k: bool(v) for k, v in self.features_json.items()})
        return merged

    def has_feature(self, feature):
        return bool(self.features.get(feature, False))

    @property
    def price_paise(self):
        """Razorpay amount hamesha smallest unit (paisa) mein maangta hai."""
        return int((self.price or 0) * 100)

    def is_unlimited_patients(self):
        return self.patient_limit == UNLIMITED or self.patient_limit < 0

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.price < 0:
            raise ValidationError({"price": "Price negative nahi ho sakta."})
        unknown = set(self.features_json or {}) - {f.value for f in Feature}
        if unknown:
            raise ValidationError({
                "features_json": f"Unknown feature keys: {sorted(unknown)}. "
                                 f"Valid: {[f.value for f in Feature]}"
            })


class SubscriptionQuerySet(models.QuerySet):
    def usable(self):
        """Jo abhi access deti hain (active/trialing/past_due grace, expire nahi hui)."""
        return self.filter(status__in=Subscription.ACCESSIBLE_STATUSES)


class SubscriptionManager(models.Manager):
    def get_queryset(self):
        return SubscriptionQuerySet(self.model, using=self._db)


class Subscription(models.Model):
    """Ek hospital ki subscription. OneToOne - ek waqt pe ek hi active plan."""

    class Status(models.TextChoices):
        TRIALING = "TRIALING", "Trialing"
        ACTIVE = "ACTIVE", "Active"
        PAST_DUE = "PAST_DUE", "Past due"
        PENDING = "PENDING", "Pending"        # Razorpay subscription bana, payment abhi nahi
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"
        HALTED = "HALTED", "Halted"          # Razorpay: payment failures ke baad rok diya

    # Kaunse statuses mein app access milta hai.
    # PAST_DUE ko grace mein rakha hai - payment fail hote hi hospital ka kaam
    # band kar dena production hospital ke liye bahut harsh hai.
    ACCESSIBLE_STATUSES = (
        Status.TRIALING, Status.ACTIVE, Status.PAST_DUE, Status.PENDING,
    )

    hospital = models.OneToOneField(
        "tenants.Hospital", on_delete=models.CASCADE, related_name="subscription",
        null=True, blank=True,
        help_text="NULL = platform-level / unassigned (tenant backfill ke liye)",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)

    # ---------------- Razorpay pointers ----------------
    razorpay_subscription_id = models.CharField(max_length=60, blank=True, db_index=True)
    razorpay_customer_id = models.CharField(max_length=60, blank=True)
    razorpay_plan_id = models.CharField(max_length=60, blank=True)
    last_payment_id = models.CharField(max_length=60, blank=True)
    last_payment_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SubscriptionManager()
    # Tenant manager se bachne ke liye (yeh OneToOne nullable hai):
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "current_period_end"])]

    def __str__(self):
        return f"{self.plan.name} for {self.hospital_id or 'unassigned'} ({self.status})"

    # ---------------- status helpers ----------------
    @property
    def is_accessible(self):
        """Kya hospital ko abhi app use karne ka access milna chahiye?"""
        if self.status not in self.ACCESSIBLE_STATUSES:
            return False
        # Period khatam ho gaya ho to EXPIRED maano (DB status stale ho sakta hai)
        if self.current_period_end and self.current_period_end < timezone.now():
            return self.status == self.Status.PAST_DUE and self._within_grace()
        return True

    def _within_grace(self):
        grace_days = getattr(settings, "SUBSCRIPTION_GRACE_DAYS", 3)
        if not self.current_period_end:
            return False
        return timezone.now() <= self.current_period_end + timezone.timedelta(days=grace_days)

    @property
    def is_expired(self):
        return bool(self.current_period_end and self.current_period_end < timezone.now())

    @property
    def days_remaining(self):
        if not self.current_period_end:
            return None
        return (self.current_period_end - timezone.now()).days

    def has_feature(self, feature):
        return self.is_accessible and self.plan.has_feature(feature)

    # ---------------- lookups ----------------
    @classmethod
    def for_hospital(cls, hospital):
        """Hospital ki subscription, ya None. Kabhi exception nahi."""
        if hospital is None:
            return None
        return cls.all_objects.filter(hospital=hospital).first()

    def mark_active(self, period_end=None, payment_id=None):
        self.status = self.Status.ACTIVE
        self.current_period_start = timezone.now()
        if period_end:
            self.current_period_end = period_end
        if payment_id:
            self.last_payment_id = payment_id
            self.last_payment_at = timezone.now()
        self.failure_count = 0
        self.save()
        return self

    def mark_payment_failed(self):
        self.failure_count += 1
        self.status = self.Status.PAST_DUE
        self.save(update_fields=["failure_count", "status", "updated_at"])
        return self


class PaymentEvent(models.Model):
    """
    Razorpay webhook ka audit trail. Signature verify hone ke BAAD hi likha jaata
    hai, isliye "payment hua tha ya nahi" ka jhagda ho to yahan se pata chalega.
    Duplicate deliveries ko ignore karne ke liye event_id unique hai.
    """

    event_id = models.CharField(max_length=80, unique=True, db_index=True)
    event_type = models.CharField(max_length=80)
    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    payload = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False)
    error = models.CharField(max_length=255, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event_type} ({self.event_id})"
