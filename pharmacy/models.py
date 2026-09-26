from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from patients.models import Patient
from doctors.models import Doctor
from tenants.models import TenantModel


class Medicine(TenantModel):
    name = models.CharField(max_length=150)
    manufacturer = models.CharField(max_length=150, blank=True)
    batch_number = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    stock_quantity = models.PositiveIntegerField(default=0)
    reorder_level = models.PositiveIntegerField(default=20, help_text="Alert when stock falls below this")
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.stock_quantity} in stock)"

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.reorder_level

    @property
    def is_expired(self):
        from django.utils import timezone
        return bool(self.expiry_date and self.expiry_date < timezone.now().date())


class Prescription(TenantModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='prescriptions')
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True, related_name='prescriptions')
    notes = models.TextField(blank=True)
    is_billed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Prescription #{self.pk} - {self.patient.full_name}"

    @property
    def total_cost(self):
        return sum(item.subtotal for item in self.items.all())


class PrescriptionItem(TenantModel):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name='items')
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name='prescription_items')
    quantity = models.PositiveIntegerField()
    dosage_instructions = models.CharField(max_length=150, blank=True, help_text="e.g. 1 tablet twice daily after food")

    def __str__(self):
        return f"{self.medicine.name} x{self.quantity}"

    @property
    def subtotal(self):
        return self.quantity * self.medicine.unit_price

    def clean(self):
        if self.medicine_id and self.quantity and self.quantity > self.medicine.stock_quantity:
            raise ValidationError(
                f"Only {self.medicine.stock_quantity} units of {self.medicine.name} left in stock."
            )
