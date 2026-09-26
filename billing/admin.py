from django.contrib import admin
from .models import Invoice, InvoiceItem, Payment
from tenants.admin_mixins import TenantAdminMixin

class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0

class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ('transaction_id',)

@admin.register(Invoice)
class InvoiceAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('invoice_number', 'patient', 'total_amount', 'amount_paid', 'balance_due', 'status', 'created_at')
    readonly_fields = ('invoice_number',)
    inlines = [InvoiceItemInline, PaymentInline]
    search_fields = ('invoice_number', 'patient__first_name', 'patient__patient_id')
