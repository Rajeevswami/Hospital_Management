"""
Django admin ko tenant-aware banata hai - bina existing admin registrations tode.

Behaviour:
  * Tenant admin (user.hospital set)  -> sirf apne hospital ka data dikhega,
    naye records apne hospital mein banenge, hospital field form mein nahi dikhega.
  * Platform super-admin (hospital=None, is_platform_admin=True) -> sab kuch dikhega,
    hospital field editable rahega.

Isko har ModelAdmin mein mix karo:  class PatientAdmin(TenantAdminMixin, admin.ModelAdmin)
"""
from django.contrib import admin

from .context import get_current_hospital


class TenantAdminMixin:
    def get_queryset(self, request):
        qs = self.model.all_objects.all()
        ordering = self.get_ordering(request)
        if ordering:
            qs = qs.order_by(*ordering)
        hospital = getattr(request.user, "hospital", None)
        if hospital is not None:
            qs = qs.filter(hospital=hospital)
        elif not getattr(request.user, "is_platform_admin", False) and not request.user.is_superuser:
            # Na tenant hai na platform rights -> kuch mat dikhao
            qs = qs.none()
        return qs

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if self._is_tenant_scoped(request):
            fieldsets = [
                (name, {**opts, "fields": tuple(f for f in opts["fields"] if f != "hospital")})
                for name, opts in fieldsets
            ]
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        # Tenant admin hospital badal nahi sakta (data ek tenant se doosre mein move nahi hoga)
        if self._is_tenant_scoped(request) and "hospital" not in readonly:
            readonly.append("hospital")
        return readonly

    def save_model(self, request, obj, form, change):
        if obj.hospital_id is None:
            obj.hospital = getattr(request.user, "hospital", None)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """FK dropdowns (patient/doctor/bed...) bhi current tenant tak limited."""
        hospital = getattr(request.user, "hospital", None)
        if hospital is not None:
            related = getattr(db_field, "related_model", None)
            if related is not None and hasattr(related, "all_objects") and related is not self.model:
                if any(f.name == "hospital" for f in related._meta.get_fields()):
                    kwargs["queryset"] = related.all_objects.filter(hospital=hospital)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @staticmethod
    def _is_tenant_scoped(request):
        return getattr(request.user, "hospital", None) is not None


class TenantAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Ready-made base class - naye tenant models ke liye."""

    list_display_extra = ()

    def get_list_display(self, request):
        base = list(super().get_list_display(request))
        if getattr(request.user, "is_platform_admin", False):
            return ["hospital"] + [f for f in base if f != "hospital"]
        return base
