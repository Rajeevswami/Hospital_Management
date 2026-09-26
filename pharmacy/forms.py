from django import forms
from django.forms import inlineformset_factory
from .models import Medicine, Prescription, PrescriptionItem
from patients.models import Patient
from doctors.models import Doctor
from tenants.forms import TenantModelForm


class MedicineForm(TenantModelForm):
    class Meta:
        model = Medicine
        fields = ['name', 'manufacturer', 'batch_number', 'unit_price', 'stock_quantity', 'reorder_level', 'expiry_date']
        widgets = {'expiry_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name not in self.Meta.widgets:
                field.widget.attrs['class'] = 'form-control'


class PrescriptionForm(TenantModelForm):
    class Meta:
        model = Prescription
        fields = ['patient', 'doctor', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['patient'].widget.attrs['class'] = 'form-select'
        self.fields['doctor'].widget.attrs['class'] = 'form-select'
        self.fields['doctor'].queryset = Doctor.objects.filter(is_active=True)


class PrescriptionItemForm(TenantModelForm):
    class Meta:
        model = PrescriptionItem
        fields = ['medicine', 'quantity', 'dosage_instructions']
        widgets = {
            'medicine': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'dosage_instructions': forms.TextInput(attrs={'class': 'form-control'}),
        }


# Lets a doctor add multiple medicines to one prescription in a single form submit
PrescriptionItemFormSet = inlineformset_factory(
    Prescription, PrescriptionItem, form=PrescriptionItemForm,
    extra=3, can_delete=True,
)
