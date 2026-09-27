"""
Tenant-aware form helpers.

Why this was needed: ModelForm's metaclass captures the FK dropdown's
queryset at CLASS DEFINITION time (no tenant is active then, so the manager
returns an unscoped queryset). When the form is used in a request, the
queryset must be re-scoped - otherwise another hospital's patients/doctors
would show up in the dropdown.

That is why every ModelForm is built with this mixin.
"""
from django import forms

from .managers import TenantQuerySet


class TenantFormMixin:
    """Limit every ModelChoiceField's queryset to the current tenant."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            qs = getattr(field, "queryset", None)
            if isinstance(qs, TenantQuerySet):
                field.queryset = qs.for_tenant()


class TenantModelForm(TenantFormMixin, forms.ModelForm):
    """Drop-in replacement for forms.ModelForm in a multi-tenant app."""
