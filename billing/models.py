from django.db import models, transaction, IntegrityError
from django.conf import settings
from django.utils import timezone
from patients.models import Patient
from tenants.models import TenantModel


class Invoice(TenantModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
        PAID = 'PAID', 'Paid'
        CANCELLED = 'CANCELLED', 'Cancelled'

    # MULTI-TENANT: per-hospital unique (pehle globally unique tha)
    invoice_number = models.CharField(max_length=20, editable=False, db_index=True)
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='invoices')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['hospital', 'invoice_number'], name='uniq_invoice_number_per_hospital'
            ),
        ]

    def __str__(self):
        return f"{self.invoice_number} - {self.patient.full_name}"

    @property
    def total_amount(self):
        return sum(item.amount for item in self.items.all())

    @property
    def amount_paid(self):
        return sum(p.amount for p in self.payments.all())

    @property
    def balance_due(self):
        return self.total_amount - self.amount_paid

    def refresh_status(self):
        """Recompute status from payments. Call after recording any payment."""
        if self.status == self.Status.CANCELLED:
            return
        paid = self.amount_paid
        total = self.total_amount
        if paid <= 0:
            self.status = self.Status.PENDING
        elif paid < total:
            self.status = self.Status.PARTIALLY_PAID
        else:
            self.status = self.Status.PAID
        self.save(update_fields=['status'])

    def save(self, *args, **kwargs):
        self.ensure_hospital()
        if not self.invoice_number:
            from core.models import IDCounter
            year = timezone.now().year
            next_num = IDCounter.get_next(f'invoice_{year}', hospital=self.hospital)
            self.invoice_number = f"INV-{year}-{next_num:04d}"
        super().save(*args, **kwargs)


class InvoiceItem(TenantModel):
    class ItemType(models.TextChoices):
        CONSULTATION = 'CONSULTATION', 'Consultation Fee'
        ADMISSION = 'ADMISSION', 'Admission / Bed Charges'
        MEDICINE = 'MEDICINE', 'Medicine / Prescription'
        OTHER = 'OTHER', 'Other Charges'

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    item_type = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.OTHER)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.description} - ₹{self.amount}"


class Payment(TenantModel):
    class Mode(models.TextChoices):
        CASH = 'CASH', 'Cash'
        UPI = 'UPI', 'UPI'
        CARD = 'CARD', 'Card'
        NET_BANKING = 'NET_BANKING', 'Net Banking'

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.CASH)
    transaction_id = models.CharField(max_length=50, unique=True, editable=False)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f"{self.transaction_id} - ₹{self.amount} ({self.mode})"

    def save(self, *args, **kwargs):
        if not self.transaction_id:
            import uuid
            self.transaction_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)
