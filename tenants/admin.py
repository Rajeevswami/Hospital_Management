"""
Hospital admin - sirf platform super-admin ke liye (SaaS operator).
Hospital staff yahan nahi aa sakta: TenantAdminMixin unka queryset none() kar deta hai.
"""
from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import Hospital

User = get_user_model()


@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'staff_count', 'contact_email', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug', 'contact_email')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')

    def staff_count(self, obj):
        return User.all_objects.filter(hospital=obj).count()
    staff_count.short_description = 'Staff'

    def has_add_permission(self, request):
        return request.user.is_superuser or getattr(request.user, 'is_platform_admin', False)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser or getattr(request.user, 'is_platform_admin', False)

    def has_delete_permission(self, request, obj=None):
        # Tenant delete karna = customer ka poora data delete. Sirf superuser.
        return request.user.is_superuser

    def get_queryset(self, request):
        return super().get_queryset(request).select_related()
