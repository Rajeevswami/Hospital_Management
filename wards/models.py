from django.db import models
from django.conf import settings
from patients.models import Patient
from doctors.models import Doctor
from tenants.models import TenantModel


class Ward(TenantModel):
    class WardType(models.TextChoices):
        GENERAL = 'GENERAL', 'General'
        ICU = 'ICU', 'ICU'
        PRIVATE = 'PRIVATE', 'Private'
        EMERGENCY = 'EMERGENCY', 'Emergency'

    name = models.CharField(max_length=100)
    ward_type = models.CharField(max_length=20, choices=WardType.choices, default=WardType.GENERAL)
    charge_per_day = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.name} ({self.get_ward_type_display()})"


class Bed(TenantModel):
    ward = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name='beds')
    bed_number = models.CharField(max_length=10)
    is_occupied = models.BooleanField(default=False)

    class Meta:
        unique_together = ('ward', 'bed_number')
        ordering = ['ward', 'bed_number']

    def __str__(self):
        status = "Occupied" if self.is_occupied else "Free"
        return f"{self.ward.name} - Bed {self.bed_number} ({status})"


class Admission(TenantModel):
    """
    The core 'patient was admitted, with which disease' record.
    One active admission per patient at a time (enforced in the view, not DB,
    since a patient can be admitted again after discharge).
    """
    class Status(models.TextChoices):
        ADMITTED = 'ADMITTED', 'Admitted'
        DISCHARGED = 'DISCHARGED', 'Discharged'

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='admissions')
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True, related_name='admissions')
    bed = models.ForeignKey(Bed, on_delete=models.SET_NULL, null=True, blank=True, related_name='admissions')

    diagnosis = models.CharField(max_length=255, help_text="Disease / condition the patient was admitted for")
    notes = models.TextField(blank=True, help_text="Treatment notes, observations")

    admission_date = models.DateTimeField(auto_now_add=True)
    discharge_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ADMITTED)
    is_billed = models.BooleanField(default=False)

    admitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='admissions_created'
    )

    class Meta:
        ordering = ['-admission_date']

    def __str__(self):
        return f"{self.patient.patient_id} - {self.diagnosis} ({self.status})"

    @property
    def days_admitted(self):
        from django.utils import timezone
        end = self.discharge_date or timezone.now()
        return max((end - self.admission_date).days, 1)
