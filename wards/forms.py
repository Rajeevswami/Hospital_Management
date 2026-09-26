from django import forms
from .models import Admission, Bed
from patients.models import Patient
from doctors.models import Doctor
from tenants.forms import TenantModelForm


class AdmissionForm(TenantModelForm):
    class Meta:
        model = Admission
        fields = ['patient', 'doctor', 'bed', 'diagnosis', 'notes']
        widgets = {
            'diagnosis': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Dengue Fever, Fracture - left leg'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['patient'].widget.attrs['class'] = 'form-select'
        self.fields['doctor'].widget.attrs['class'] = 'form-select'
        self.fields['doctor'].queryset = Doctor.objects.filter(is_active=True)
        # Only show free beds - prevents double-booking a bed
        self.fields['bed'].queryset = Bed.objects.filter(is_occupied=False).select_related('ward')
        self.fields['bed'].widget.attrs['class'] = 'form-select'

    def clean_bed(self):
        bed = self.cleaned_data.get('bed')
        if bed and bed.is_occupied:
            raise forms.ValidationError("This bed was just taken by another admission. Please pick another.")
        return bed


class DischargeForm(TenantModelForm):
    class Meta:
        model = Admission
        fields = ['notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'})}
