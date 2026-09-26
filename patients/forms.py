from django import forms
from .models import Patient
from tenants.forms import TenantModelForm


class PatientForm(TenantModelForm):
    class Meta:
        model = Patient
        fields = [
            'first_name', 'last_name', 'date_of_birth', 'gender', 'blood_group',
            'phone', 'address', 'emergency_contact_name', 'emergency_contact_phone',
            'known_allergies',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'address': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
            'known_allergies': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name not in self.Meta.widgets:
                field.widget.attrs['class'] = 'form-control' if not isinstance(field.widget, forms.Select) else 'form-select'
