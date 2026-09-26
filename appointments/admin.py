from django.contrib import admin
from .models import Appointment
from tenants.admin_mixins import TenantAdminMixin


@admin.register(Appointment)
class AppointmentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('patient', 'doctor', 'appointment_date', 'appointment_time', 'status', 'fee')
    list_filter = ('status', 'appointment_date')
    search_fields = ('patient__first_name', 'patient__patient_id')
