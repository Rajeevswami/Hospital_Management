from django import forms
from django.core.exceptions import ValidationError
from .models import Payment
from patients.models import Patient
from tenants.forms import TenantFormMixin, TenantModelForm


class PatientSelectForm(TenantFormMixin, forms.Form):
    patient = forms.ModelChoiceField(
        queryset=Patient.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )


class PaymentForm(TenantModelForm):
    class Meta:
        model = Payment
        fields = ['amount', 'mode']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'mode': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")
        if self.invoice and amount > self.invoice.balance_due:
            raise ValidationError(
                f"Amount exceeds balance due (₹{self.invoice.balance_due}). Cannot overpay an invoice."
            )
        return amount
