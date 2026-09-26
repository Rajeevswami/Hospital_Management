"""
API endpoints (Phase 4).

Auth: JWT (SimpleJWT). Token lene ke liye `POST /api/token/` - username/password
usi hospital ke subdomain (ya X-Hospital-Slug header) ke saath, kyunki login
tenant-scoped backend se hota hai.

Gating: har data endpoint pe `api_access` feature check hota hai (HasApiAccess).
Token endpoint aur schema/docs gate nahi hote - warna client ko pata hi na chale
ki API hai kya.
"""
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts.models import User
from appointments.models import Appointment
from billing.models import Invoice
from doctors.models import Doctor
from ml_engine.models import AppointmentRisk
from patients.models import Patient
from subscriptions.gating import check_feature, check_limit
from subscriptions.models import Feature, Subscription

from .permissions import HasApiAccess, IsTenantMember, ReadOnlyUnlessWriteRoles, has_role
from .serializers import (
    AppointmentRiskSerializer,
    AppointmentSerializer,
    AppointmentStatusSerializer,
    DoctorSerializer,
    HospitalSerializer,
    InvoiceSerializer,
    PatientSerializer,
    SubscriptionSummarySerializer,
    UserSerializer,
)
from .viewsets import TenantModelViewSet


class WriteRolesMixin:
    """Padhna sabko, likhna sirf in roles ko."""

    write_roles = ()

    def get_permissions(self):
        perms = super().get_permissions()
        perms.append(_write_role_permission(self.write_roles)())
        return perms


def _write_role_permission(roles):
    class _P(ReadOnlyUnlessWriteRoles):
        write_roles = roles

    return _P


# --------------------------------------------------------------------- patients
class PatientViewSet(WriteRolesMixin, TenantModelViewSet):
    """Patients CRUD. Likhna sirf ADMIN / RECEPTIONIST."""

    tenant_model = Patient
    serializer_class = PatientSerializer
    write_roles = ("ADMIN", "RECEPTIONIST")
    filter_fields = ["gender", "blood_group"]
    search_fields = ["first_name", "last_name", "patient_id", "phone"]
    ordering_fields = ["created_at", "first_name", "patient_id"]
    ordering = ["-created_at"]

    def create(self, request, *args, **kwargs):
        # Phase 2 ka patient_limit gate - API se bhi bypass nahi hona chahiye
        allowed, reason, limit = check_limit(request, "patient_limit", Patient.objects.count())
        if not allowed:
            return Response(
                {"detail": f"Patient limit ({limit}) poora ho gaya hai. Plan upgrade karo.",
                 "reason": reason},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        return super().create(request, *args, **kwargs)

    @extend_schema(responses=AppointmentSerializer(many=True))
    @action(detail=True, methods=["get"])
    def appointments(self, request, pk=None):
        """Is patient ke appointments."""
        patient = self.get_object()
        qs = Appointment.objects.filter(patient=patient).select_related("doctor__user", "risk")
        page = self.paginate_queryset(qs)
        ser = AppointmentSerializer(page if page is not None else qs, many=True,
                                    context={"request": request})
        if page is not None:
            return self.get_paginated_response(ser.data)
        return Response(ser.data)


# ---------------------------------------------------------------------- doctors
class DoctorViewSet(WriteRolesMixin, TenantModelViewSet):
    tenant_model = Doctor
    serializer_class = DoctorSerializer
    write_roles = ("ADMIN",)
    search_fields = ["user__first_name", "user__last_name", "specialization"]
    ordering_fields = ["consultation_fee", "experience_years"]
    ordering = ["user__first_name"]


# ------------------------------------------------------------------ appointments
class AppointmentViewSet(WriteRolesMixin, TenantModelViewSet):
    """
    Appointments CRUD + do useful actions:
      GET  /api/appointments/today/          aaj ke appointments
      PATCH /api/appointments/<id>/status/   COMPLETED / NO_SHOW / CANCELLED mark karo
    """

    tenant_model = Appointment
    serializer_class = AppointmentSerializer
    write_roles = ("ADMIN", "RECEPTIONIST", "DOCTOR")
    filter_fields = ["status", "doctor", "appointment_date"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_id", "reason"]
    ordering_fields = ["appointment_date", "appointment_time", "created_at"]
    ordering = ["-appointment_date", "-appointment_time"]

    def get_queryset(self):
        qs = super().get_queryset().select_related("patient", "doctor__user", "risk")
        doctor = getattr(self.request.user, "doctor_profile", None)
        # Doctor ko sirf apne appointments (existing RBAC ke hisaab se)
        if self.request.user.role == User.Role.DOCTOR and doctor is not None:
            qs = qs.filter(doctor=doctor)
        return qs

    @extend_schema(responses=AppointmentSerializer(many=True))
    @action(detail=False, methods=["get"])
    def today(self, request):
        qs = self.get_queryset().filter(appointment_date=timezone.now().date())
        page = self.paginate_queryset(qs)
        ser = AppointmentSerializer(page if page is not None else qs, many=True,
                                    context={"request": request})
        if page is not None:
            return self.get_paginated_response(ser.data)
        return Response(ser.data)

    @extend_schema(request=AppointmentStatusSerializer, responses=AppointmentSerializer)
    @action(detail=True, methods=["patch"], url_path="status")
    def set_status(self, request, pk=None):
        """Status update (billing flow wahi rehta hai - yahan sirf status)."""
        appointment = self.get_object()
        ser = AppointmentStatusSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        appointment.status = ser.validated_data["status"]
        appointment.save(update_fields=["status", "updated_at"] if hasattr(appointment, "updated_at") else ["status"])
        return Response(AppointmentSerializer(appointment, context={"request": request}).data)

    @extend_schema(responses=AppointmentRiskSerializer)
    @action(detail=True, methods=["get"])
    def risk(self, request, pk=None):
        """Phase 3 ka no-show risk (agar score bana ho)."""
        appointment = self.get_object()
        risk = getattr(appointment, "risk", None)
        if risk is None:
            return Response({"detail": "Is appointment ka abhi koi score nahi hai."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(AppointmentRiskSerializer(risk).data)


# --------------------------------------------------------------------- invoices
class InvoiceViewSet(TenantModelViewSet):
    """Invoices - read-only (payment flow HTML/PDF side pe hai)."""

    tenant_model = Invoice
    serializer_class = InvoiceSerializer
    http_method_names = ["get", "head", "options"]
    permission_classes = [IsAuthenticated, IsTenantMember, HasApiAccess,
                          has_role("ADMIN", "RECEPTIONIST")]
    filter_fields = ["status"]
    search_fields = ["invoice_number", "patient__first_name", "patient__last_name"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return super().get_queryset().select_related("patient").prefetch_related("items")


# ------------------------------------------------------------------------ staff
class StaffViewSet(TenantModelViewSet):
    """Hospital ka staff - sirf ADMIN."""

    tenant_model = User
    serializer_class = UserSerializer
    http_method_names = ["get", "head", "options"]
    permission_classes = [IsAuthenticated, IsTenantMember, HasApiAccess, has_role("ADMIN")]
    filter_fields = ["role", "is_active_staff"]
    search_fields = ["username", "first_name", "last_name", "email"]
    ordering = ["username"]


# ------------------------------------------------------------------ AI no-show
class AppointmentRiskViewSet(TenantModelViewSet):
    """
    No-show risk scores - read-only.
    NOTE: yeh `api_access` se gated hai; AI dashboard wala `ai_no_show` gate
    HTML page ke liye tha. API consumer ko scores dikhna chahiye (usne API pay kiya hai).
    """

    tenant_model = AppointmentRisk
    serializer_class = AppointmentRiskSerializer
    http_method_names = ["get", "head", "options"]
    filter_fields = ["level", "engine"]
    ordering = ["-score"]

    def get_queryset(self):
        return super().get_queryset().select_related("appointment__patient", "appointment__doctor__user")

    @extend_schema(responses=AppointmentRiskSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="high")
    def high(self, request):
        """Aane wale HIGH risk appointments (dashboard alert ka API version)."""
        qs = AppointmentRisk.high_risk_upcoming(limit=50)
        return Response(AppointmentRiskSerializer(qs, many=True).data)


# ------------------------------------------------------------- me / hospital
@extend_schema(responses=UserSerializer)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTenantMember, HasApiAccess])
def me(request):
    """Apna profile + role + hospital."""
    return Response(UserSerializer(request.user).data)


@extend_schema(responses=HospitalSerializer)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTenantMember, HasApiAccess])
def current_hospital(request):
    """Current tenant ki info (plan + subscription status ke saath)."""
    hospital = request.hospital
    if hospital is None:
        return Response({"detail": "No tenant"}, status=status.HTTP_400_BAD_REQUEST)
    sub = Subscription.for_hospital(hospital)
    hospital.subscription = sub  # serializer isi se plan padhta hai
    return Response(HospitalSerializer(hospital).data)


@extend_schema(
    responses=SubscriptionSummarySerializer,
    examples=[OpenApiExample(
        "Scale plan",
        value={"plan": "Scale", "status": "ACTIVE", "current_period_end": "2026-10-26T00:00:00Z",
               "is_accessible": True, "patient_limit": -1,
               "features": {"api_access": True, "ai_no_show": True}},
    )],
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTenantMember, HasApiAccess])
def subscription_summary(request):
    """Plan, status, limits aur feature flags - client ko UI gate karne ke liye."""
    sub = Subscription.for_hospital(request.hospital)
    if sub is None:
        return Response({"detail": "Koi subscription nahi."}, status=status.HTTP_404_NOT_FOUND)
    return Response(SubscriptionSummarySerializer({
        "plan": sub.plan.name,
        "status": sub.status,
        "current_period_end": sub.current_period_end,
        "is_accessible": sub.is_accessible,
        "patient_limit": sub.plan.patient_limit,
        "features": sub.plan.features,
    }).data)


@extend_schema(responses={"200": dict, "402": dict})
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTenantMember, HasApiAccess])
def feature_check(request):
    """
    `GET /api/features/<key>/` - ek feature allowed hai ya nahi.
    200 = allowed, 402 = plan mein nahi (reason ke saath).
    """
    key = request.query_params.get("feature", "")
    if key not in Feature.values:
        return Response({"detail": f"Unknown feature: {key!r}",
                         "valid": list(Feature.values)},
                        status=status.HTTP_400_BAD_REQUEST)
    allowed, reason = check_feature(request, key)
    if not allowed:
        return Response({"feature": key, "allowed": False, "reason": reason},
                        status=status.HTTP_402_PAYMENT_REQUIRED)
    return Response({"feature": key, "allowed": True, "reason": reason})


@extend_schema(responses={"200": dict})
@api_view(["GET"])
@permission_classes([AllowAny])
def api_root(request):
    """Discovery endpoint - kaun se resources available hain."""
    base = request.build_absolute_uri("/api/")
    return Response({
        "token": base + "token/",
        "token_refresh": base + "token/refresh/",
        "me": base + "me/",
        "hospital": base + "hospital/",
        "subscription": base + "subscription/",
        "features": base + "features/",
        "patients": base + "patients/",
        "doctors": base + "doctors/",
        "appointments": base + "appointments/",
        "appointments_today": base + "appointments/today/",
        "invoices": base + "invoices/",
        "staff": base + "staff/",
        "risks": base + "risks/",
        "risks_high": base + "risks/high/",
        "docs": base + "docs/",
        "schema": base + "schema/",
    })
