from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    # Audit trail platform-level hai (superuser only) - isliye tenant-scoped nahi,
    # par tenant-wise filter kar sakte hain.
    list_filter_extra = ('hospital',)
    list_display = ('timestamp', 'user', 'action', 'model_name', 'object_id', 'object_repr', 'ip_address')
    list_filter = ('action', 'model_name', 'hospital')
    search_fields = ('object_repr', 'user__username')
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False  # audit entries are only ever created by signals, never manually

    def has_change_permission(self, request, obj=None):
        return False  # immutable - editing the audit trail defeats its purpose

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # only superuser can prune old logs if ever needed
