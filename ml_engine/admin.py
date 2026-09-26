from django.contrib import admin

from tenants.admin_mixins import TenantAdminMixin

from .models import AppointmentRisk, NoShowModelArtifact


@admin.register(AppointmentRisk)
class AppointmentRiskAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("appointment", "score", "level", "engine", "model_version", "computed_at")
    list_filter = ("level", "engine")
    search_fields = ("appointment__patient__first_name", "appointment__patient__patient_id")
    readonly_fields = ("score", "level", "engine", "reasons", "model_version", "computed_at")

    def has_add_permission(self, request):
        return False  # score sirf engine compute karta hai


@admin.register(NoShowModelArtifact)
class NoShowModelArtifactAdmin(admin.ModelAdmin):
    # Platform-level: model artifacts ka hospital NULL ho sakta hai, isliye
    # TenantAdminMixin yahan nahi - superuser/platform admin dekhega.
    list_display = ("version", "hospital", "algorithm", "training_rows",
                    "positive_rate", "is_active", "trained_at")
    list_filter = ("is_active", "algorithm")
    search_fields = ("version", "hospital__slug")
    readonly_fields = ("version", "relative_path", "training_rows", "positive_rate",
                       "metrics", "feature_names", "feature_importances", "trained_at")

    def get_queryset(self, request):
        return NoShowModelArtifact.all_objects.all()
