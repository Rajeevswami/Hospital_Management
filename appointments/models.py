from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from patients.models import Patient
from doctors.models import Doctor
from tenants.models import TenantModel


class Appointment(TenantModel):
    class Status(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'
        NO_SHOW = 'NO_SHOW', 'No Show'

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='appointments')
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name='appointments')
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    is_billed = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments_booked'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-appointment_date', '-appointment_time']
        # A doctor can't have two SCHEDULED appointments at the exact same slot.
        # (Enforced fully in clean(); this index just speeds up the lookup.)
        indexes = [models.Index(fields=['doctor', 'appointment_date', 'appointment_time'])]

    def __str__(self):
        return f"{self.patient.full_name} with {self.doctor} on {self.appointment_date} {self.appointment_time}"

    def clean(self):
        # NOTE: self.__class__.objects is tenant-scoped, so this conflict check
        # looks only at this hospital's appointments (the same doctor-slot in
        # another hospital is irrelevant).
        conflict = self.__class__.objects.filter(
            doctor=self.doctor,
            appointment_date=self.appointment_date,
            appointment_time=self.appointment_time,
            status=self.Status.SCHEDULED,
        ).exclude(pk=self.pk)
        if conflict.exists():
            raise ValidationError("This doctor already has a scheduled appointment at this exact time.")

    def save(self, *args, **kwargs):
        # hospital first (otherwise full_clean says "cannot be null"), then fee, then validate
        self.ensure_hospital()
        if self.fee is None:
            self.fee = self.doctor.consultation_fee
        self.full_clean()
        super().save(*args, **kwargs)
