from django.contrib import admin
from .models import Patient
from tenants.admin_mixins import TenantAdminMixin


@admin.register(Patient)
class PatientAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('patient_id', 'full_name', 'age', 'gender', 'phone', 'blood_group', 'created_at')
    search_fields = ('patient_id', 'first_name', 'last_name', 'phone')
    list_filter = ('gender', 'blood_group')
    readonly_fields = ('patient_id',)
