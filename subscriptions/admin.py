from django.contrib import admin

from tenants.admin_mixins import TenantAdminMixin

from .models import PaymentEvent, Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "price", "interval", "patient_limit",
                    "staff_limit", "razorpay_plan_id", "is_active", "sort_order")
    list_filter = ("interval", "is_active")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "code", "description", "is_active", "sort_order")}),
        ("Pricing", {"fields": ("price", "currency", "interval", "trial_days")}),
        ("Limits", {"fields": ("patient_limit", "staff_limit", "appointment_limit"),
                    "description": "-1 = unlimited"}),
        ("Features", {"fields": ("features_json",)}),
        ("Razorpay", {"fields": ("razorpay_plan_id",),
                      "description": "Khaali chhodo to `ensure_plan` command bana dega."}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def has_add_permission(self, request):
        return request.user.is_superuser or getattr(request.user, "is_platform_admin", False)


@admin.register(Subscription)
class SubscriptionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "hospital", "plan", "status", "current_period_end",
                    "razorpay_subscription_id", "failure_count")
    list_filter = ("status", "plan")
    search_fields = ("hospital__name", "hospital__slug", "razorpay_subscription_id")
    readonly_fields = ("created_at", "updated_at", "last_payment_at", "failure_count")
    autocomplete_fields = ()


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "event_type", "subscription", "processed", "error", "received_at")
    list_filter = ("event_type", "processed")
    search_fields = ("event_id", "event_type")
    readonly_fields = [f.name for f in PaymentEvent._meta.fields]

    def has_add_permission(self, request):
        return False  # sirf webhook likhta hai
