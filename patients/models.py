from django.db import models, transaction, IntegrityError
from django.conf import settings
from django.utils import timezone

from tenants.models import TenantModel


class Patient(TenantModel):
    class Gender(models.TextChoices):
        MALE = 'M', 'Male'
        FEMALE = 'F', 'Female'
        OTHER = 'O', 'Other'

    class BloodGroup(models.TextChoices):
        A_POS = 'A+', 'A+'
        A_NEG = 'A-', 'A-'
        B_POS = 'B+', 'B+'
        B_NEG = 'B-', 'B-'
        AB_POS = 'AB+', 'AB+'
        AB_NEG = 'AB-', 'AB-'
        O_POS = 'O+', 'O+'
        O_NEG = 'O-', 'O-'
        UNKNOWN = 'UNK', 'Unknown'

    # MULTI-TENANT: no longer globally unique - unique per-hospital. Two
    # hospitals can both have PAT-2026-0001; old IDs stay exactly as they were.
    patient_id = models.CharField(max_length=20, editable=False, db_index=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=1, choices=Gender.choices)
    blood_group = models.CharField(max_length=3, choices=BloodGroup.choices, default=BloodGroup.UNKNOWN)
    phone = models.CharField(max_length=15)
    address = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=15, blank=True)
    known_allergies = models.TextField(blank=True, help_text="Comma separated, e.g. Penicillin, Dust")

    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='patients_registered'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['hospital', 'patient_id'], name='uniq_patient_id_per_hospital'),
        ]

    def __str__(self):
        return f"{self.patient_id} - {self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self):
        today = timezone.now().date()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    def save(self, *args, **kwargs):
        # Generate human-readable ID only once, on creation: PAT-2026-0001
        # Uses a dedicated counter row (core.IDCounter) so it's safe even when
        # many receptionists register patients at the exact same instant.
        # resolve hospital first (the ID must be generated before the INSERT)
        self.ensure_hospital()
        if not self.patient_id:
            from core.models import IDCounter
            year = timezone.now().year
            next_num = IDCounter.get_next(f'patient_{year}', hospital=self.hospital)
            self.patient_id = f"PAT-{year}-{next_num:04d}"
        super().save(*args, **kwargs)
