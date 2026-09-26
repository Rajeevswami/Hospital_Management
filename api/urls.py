"""
API routes (Phase 4).

    GET  /api/                     discovery (kaun se endpoints hain)
    POST /api/token/               JWT access + refresh (tenant-scoped login)
    POST /api/token/refresh/       refresh token se naya access token
    POST /api/token/verify/        token valid hai ya nahi
    GET  /api/me/                  apna profile
    GET  /api/hospital/            current tenant + plan
    GET  /api/subscription/        plan/limits/feature flags
    GET  /api/features/?feature=x  ek feature allowed hai ya nahi
    CRUD /api/patients/ /api/doctors/ /api/appointments/ /api/invoices/ /api/staff/
    GET  /api/risks/               no-show risk scores (+ /api/risks/high/)
    GET  /api/schema/              OpenAPI 3 schema (YAML/JSON)
    GET  /api/docs/                Swagger UI
"""
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .jwt import TenantTokenObtainPairView

from . import views

router = DefaultRouter()
router.register("patients", views.PatientViewSet, basename="api-patients")
router.register("doctors", views.DoctorViewSet, basename="api-doctors")
router.register("appointments", views.AppointmentViewSet, basename="api-appointments")
router.register("invoices", views.InvoiceViewSet, basename="api-invoices")
router.register("staff", views.StaffViewSet, basename="api-staff")
router.register("risks", views.AppointmentRiskViewSet, basename="api-risks")

urlpatterns = [
    path("", views.api_root, name="api-root"),

    # ---- JWT auth ----
    path("token/", TenantTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token_verify"),

    # ---- context ----
    path("me/", views.me, name="api-me"),
    path("hospital/", views.current_hospital, name="api-hospital"),
    path("subscription/", views.subscription_summary, name="api-subscription"),
    path("features/", views.feature_check, name="api-features"),

    # ---- resources ----
    path("", include(router.urls)),

    # ---- OpenAPI / Swagger ----
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),

    # Browsable API ke auth links (DRF ka built-in login/logout)
    path("auth/", include("rest_framework.urls")),
]
