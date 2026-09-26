"""
Tenant-scoped manager/queryset.

Har business model isko use karta hai:

    class Patient(TenantModel):
        ...

Iska matlab:
    Patient.objects.all()      -> sirf current hospital ke patients
    Patient.all_objects.all()  -> sab hospitals (sirf platform-level code ke liye)

Do safety rails:
  * Tenant context set nahi hai -> ImproperlyConfigured (silent empty result nahi).
  * Naya record bina hospital ke save hua -> hospital khud assign ho jaata hai,
    warna clear error.
"""
from django.apps import apps
from django.db import models

from . import context


def _tenant_enforcement_active():
    """
    Tenant scoping kab LAAGU hogi:
      * app registry ready ho (warna hum abhi import phase mein hain), AUR
      * ek HTTP request chal rahi ho (TenantMiddleware ne flag set kiya ho)

    Import-time pe ModelForm ki metaclass FK ka default manager evaluate karti
    hai - us waqt enforcement OFF rehti hai warna app start hi nahi hota.
    Request-time pe ON: bina tenant ke query = loud error, chup-chaap leak nahi.
    """
    return apps.ready and context.is_request_active()


class TenantQuerySet(models.QuerySet):
    def for_tenant(self):
        """Explicit tenant scoping - middleware/views mein clear intent ke liye."""
        if not _tenant_enforcement_active():
            return self  # import-time / management command / shell / tests
        hospital = context.require_current_hospital(self.model.__name__)
        if hospital is None:
            return self  # all_tenants() context
        return self._clone().filter(hospital=hospital)


class TenantManager(models.Manager):
    """Default manager - hamesha current tenant tak scoped."""

    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db).for_tenant()

    def for_hospital(self, hospital):
        """Kisi specific hospital ka data (tenant context ki zaroorat nahi)."""
        return TenantQuerySet(self.model, using=self._db).filter(hospital=hospital)

    def cross_tenant(self):
        """Sab hospitals - platform admin / global reports ke liye."""
        return TenantQuerySet(self.model, using=self._db)

    # Django ke internals (session auth, dumpdata, natural keys) default manager se
    # yeh method maangte hain. Username globally unique nahi raha, isliye pehla
    # match lete hain - tenant ka asli faisla auth backend (get_user) karta hai.
    def get_by_natural_key(self, username):
        return self.model.all_objects.filter(**{self.model.USERNAME_FIELD: username}).first()


class UnscopedManager(models.Manager):
    """
    Escape hatch: `Model.all_objects` - koi tenant filter nahi.
    Sirf admin, platform tooling aur tests mein use karo.
    """

    def get_queryset(self):
        return models.QuerySet(self.model, using=self._db)
