from django.contrib import admin
from .models import Medicine, Prescription, PrescriptionItem
from tenants.admin_mixins import TenantAdminMixin

@admin.register(Medicine)
class MedicineAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'manufacturer', 'unit_price', 'stock_quantity', 'reorder_level', 'expiry_date')
    search_fields = ('name', 'manufacturer')

class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 1

@admin.register(Prescription)
class PrescriptionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('patient', 'doctor', 'created_at')
    inlines = [PrescriptionItemInline]
