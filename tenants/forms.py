"""
Tenant-aware form helpers.

Zaroorat kyun padi: ModelForm ki metaclass FK dropdown ka queryset CLASS DEFINE
hote waqt hi capture kar leti hai (us waqt koi tenant active nahi hota, isliye
manager unscoped queryset deta hai). Form jab request mein use hoti hai tab
queryset re-scope karna zaroori hai - warna doosre hospital ke patients/doctors
dropdown mein dikh jaate.

Isliye har ModelForm is mixin ke saath banayi gayi hai.
"""
from django import forms

from .managers import TenantQuerySet


class TenantFormMixin:
    """Har ModelChoiceField ka queryset current tenant tak limit karo."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            qs = getattr(field, "queryset", None)
            if isinstance(qs, TenantQuerySet):
                field.queryset = qs.for_tenant()


class TenantModelForm(TenantFormMixin, forms.ModelForm):
    """Drop-in replacement for forms.ModelForm in a multi-tenant app."""
