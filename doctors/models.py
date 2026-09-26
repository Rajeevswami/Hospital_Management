from django.db import models
from django.conf import settings

from tenants.models import TenantModel


class Doctor(TenantModel):
    """
    Links to a User with role=DOCTOR (that user logs in to see their own
    appointments/patients). One Doctor profile per doctor login.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='doctor_profile')
    specialization = models.CharField(max_length=100)
    qualification = models.CharField(max_length=150, blank=True)
    experience_years = models.PositiveIntegerField(default=0)
    consultation_fee = models.DecimalField(max_digits=8, decimal_places=2, default=500)
    available_days = models.CharField(
        max_length=100, default='Mon,Tue,Wed,Thu,Fri',
        help_text="Comma separated: Mon,Tue,Wed,Thu,Fri,Sat,Sun"
    )
    available_from = models.TimeField(default='09:00')
    available_to = models.TimeField(default='17:00')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['user__first_name']

    def __str__(self):
        return f"Dr. {self.user.get_full_name()} ({self.specialization})"

    @property
    def available_days_list(self):
        return [d.strip() for d in self.available_days.split(',')]
