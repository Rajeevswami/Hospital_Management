"""
DRF serializers.

Multi-tenant rules:
  * `hospital` kabhi client se accept NAHI hota - server request.hospital se set karta hai.
  * FK fields (patient/doctor) tenant-scoped manager se validate hote hain, isliye
    doosre hospital ka pk bheja to "invalid pk" milega (leak nahi).
"""
from rest_framework import serializers
from rest_framework.relations import ManyRelatedField, PrimaryKeyRelatedField

from accounts.models import User
from appointments.models import Appointment
from billing.models import Invoice, InvoiceItem
from doctors.models import Doctor
from ml_engine.models import AppointmentRisk
from patients.models import Patient
from tenants.models import Hospital


class TenantModelSerializer(serializers.ModelSerializer):
    """
    FK fields ko REQUEST ke tenant tak scope karta hai.

    Yeh zaroori hai: `PrimaryKeyRelatedField(queryset=Patient.objects.all())`
    class-definition (import) ke waqt evaluate hota hai, jab tenant enforcement
    OFF hoti hai. Wo queryset object phir wahi rehta hai - UNSCOPED. Matlab
    client doosre hospital ka patient pk bhej kar validate kara leta = cross-tenant
    write. Isliye har serializer instance (har request) pe queryset dobara bandha
    jaata hai. HTML forms ka `TenantModelForm` bhi yahi karta hai.
    """

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        hospital = getattr(request, "hospital", None) if request is not None else None
        for field in fields.values():
            self._rescope(field, hospital)
        return fields

    @staticmethod
    def _rescope(field, hospital):
        target = field.child_relation if isinstance(field, ManyRelatedField) else field
        if not isinstance(target, PrimaryKeyRelatedField) or target.queryset is None:
            return
        model = target.queryset.model
        if hospital is None or not hasattr(model, "all_objects"):
            return
        # for_hospital(): tenant context ki zaroorat nahi, explicit filter
        target.queryset = model.objects.for_hospital(hospital)


class HospitalSerializer(serializers.ModelSerializer):
    plan = serializers.SerializerMethodField()
    subscription_status = serializers.SerializerMethodField()

    class Meta:
        model = Hospital
        fields = ["id", "name", "slug", "plan", "subscription_status", "created_at"]
        read_only_fields = fields

    def get_plan(self, obj):
        sub = getattr(obj, "subscription", None)
        return sub.plan.name if sub and sub.plan_id else None

    def get_subscription_status(self, obj):
        sub = getattr(obj, "subscription", None)
        return sub.status if sub else None


class UserSerializer(serializers.ModelSerializer):
    hospital = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "phone",
                  "role", "hospital", "is_active_staff", "last_login", "date_joined"]
        read_only_fields = ["id", "hospital", "last_login", "date_joined"]


class PatientSerializer(TenantModelSerializer):
    full_name = serializers.CharField(read_only=True)
    age = serializers.IntegerField(read_only=True)
    hospital = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    appointments_count = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = ["id", "patient_id", "first_name", "last_name", "full_name", "age",
                  "date_of_birth", "gender", "blood_group", "phone", "address",
                  "emergency_contact_name", "emergency_contact_phone", "known_allergies",
                  "appointments_count", "hospital", "created_at", "updated_at"]
        read_only_fields = ["id", "patient_id", "hospital", "created_at", "updated_at"]

    def get_appointments_count(self, obj):
        return obj.appointments.count()


class DoctorSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    specialization = serializers.CharField()
    hospital = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Doctor
        fields = ["id", "name", "specialization", "qualification", "experience_years",
                  "consultation_fee", "available_days", "available_from", "available_to",
                  "is_active", "hospital"]
        read_only_fields = ["id", "hospital"]

    def get_name(self, obj):
        return obj.user.get_full_name() or obj.user.username


class AppointmentRiskSerializer(serializers.ModelSerializer):
    score_pct = serializers.FloatField(read_only=True)

    class Meta:
        model = AppointmentRisk
        fields = ["score", "score_pct", "level", "engine", "reasons",
                  "model_version", "computed_at"]
        read_only_fields = fields


class AppointmentSerializer(TenantModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    doctor_name = serializers.SerializerMethodField()
    risk = AppointmentRiskSerializer(read_only=True)
    hospital = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    # FK: tenant-scoped manager se validate (doosre hospital ka pk reject)
    patient = serializers.PrimaryKeyRelatedField(queryset=Patient.objects.all())
    doctor = serializers.PrimaryKeyRelatedField(queryset=Doctor.objects.all())

    class Meta:
        model = Appointment
        fields = ["id", "patient", "patient_name", "doctor", "doctor_name",
                  "appointment_date", "appointment_time", "reason", "status", "fee",
                  "is_billed", "risk", "hospital", "created_at"]
        read_only_fields = ["id", "hospital", "created_at", "is_billed"]

    def get_doctor_name(self, obj):
        return obj.doctor.user.get_full_name() if obj.doctor_id else None

    def validate(self, attrs):
        # Model.clean() wahi conflict check karta hai jo HTML form karta hai
        instance = Appointment(**{**self._writable_defaults(), **attrs})
        instance.pk = self.instance.pk if self.instance else None
        instance.ensure_hospital()
        instance.clean()
        return attrs

    def _writable_defaults(self):
        base = {}
        if self.instance is not None:
            for f in ("patient", "doctor", "appointment_date", "appointment_time", "status"):
                base[f] = getattr(self.instance, f)
        return base


class AppointmentStatusSerializer(serializers.Serializer):
    """PATCH /api/appointments/<id>/status/ ke liye."""

    status = serializers.ChoiceField(choices=Appointment.Status.choices)


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = ["id", "item_type", "description", "amount"]
        read_only_fields = ["id"]


class InvoiceSerializer(TenantModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    items = InvoiceItemSerializer(many=True, read_only=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    amount_paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    hospital = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Invoice
        fields = ["id", "invoice_number", "patient", "patient_name", "status", "items",
                  "total_amount", "amount_paid", "balance_due", "hospital", "created_at"]
        read_only_fields = fields


class TokenObtainSerializer(serializers.Serializer):
    """Swagger docs ke liye - actual validation SimpleJWT karta hai."""

    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class SubscriptionSummarySerializer(serializers.Serializer):
    plan = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    current_period_end = serializers.DateTimeField(allow_null=True)
    is_accessible = serializers.BooleanField()
    patient_limit = serializers.IntegerField(allow_null=True)
    features = serializers.DictField(child=serializers.BooleanField())
