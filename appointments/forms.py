from django import forms
from django.core.exceptions import ValidationError
from .models import Appointment
from doctors.models import Doctor
from tenants.forms import TenantModelForm


class AppointmentForm(TenantModelForm):
    class Meta:
        model = Appointment
        fields = ['patient', 'doctor', 'appointment_date', 'appointment_time', 'reason']
        widgets = {
            'appointment_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'appointment_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'reason': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Reason for visit'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['patient'].widget.attrs['class'] = 'form-select'
        self.fields['doctor'].widget.attrs['class'] = 'form-select'
        self.fields['doctor'].queryset = Doctor.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        doctor = cleaned.get('doctor')
        date = cleaned.get('appointment_date')
        time = cleaned.get('appointment_time')
        if doctor and date and time:
            conflict = Appointment.objects.filter(
                doctor=doctor, appointment_date=date, appointment_time=time, status=Appointment.Status.SCHEDULED
            ).exclude(pk=self.instance.pk)
            if conflict.exists():
                raise ValidationError("This doctor is already booked at this exact date/time. Pick another slot.")
        return cleaned
