"""
Tenant-scoped manager/queryset.

Every business model uses it:

    class Patient(TenantModel):
        ...

This means:
    Patient.objects.all()      -> only the current hospital's patients
    Patient.all_objects.all()  -> all hospitals (for platform-level code only)

Two safety rails:
  * No tenant context set -> ImproperlyConfigured (not a silent empty result).
  * A new record saved without a hospital -> the hospital is assigned
    automatically, otherwise a clear error.
"""
from django.apps import apps
from django.db import models

from . import context


def _tenant_enforcement_active():
    """
    When tenant scoping APPLIES:
      * the app registry is ready (otherwise we are still in the import phase),
        AND
      * an HTTP request is running (TenantMiddleware set the flag)

    At import time, ModelForm's metaclass evaluates the FK's default manager -
    enforcement stays OFF then, otherwise the app would not even start.
    At request time it is ON: a query without a tenant = loud error, never a
    quiet leak.
    """
    return apps.ready and context.is_request_active()


class TenantQuerySet(models.QuerySet):
    def for_tenant(self):
        """Explicit tenant scoping - for a clear intent in middleware/views."""
        if not _tenant_enforcement_active():
            return self  # import-time / management command / shell / tests
        hospital = context.require_current_hospital(self.model.__name__)
        if hospital is None:
            return self  # all_tenants() context
        return self._clone().filter(hospital=hospital)


class TenantManager(models.Manager):
    """Default manager - always scoped to the current tenant."""

    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db).for_tenant()

    def for_hospital(self, hospital):
        """Data of a specific hospital (no tenant context needed)."""
        return TenantQuerySet(self.model, using=self._db).filter(hospital=hospital)

    def cross_tenant(self):
        """All hospitals - for platform admin / global reports."""
        return TenantQuerySet(self.model, using=self._db)

    # Django's internals (session auth, dumpdata, natural keys) request this
    # method from the default manager. Usernames are no longer globally unique,
    # so we take the first match - the real tenant decision is made by the auth
    # backend (get_user).
    def get_by_natural_key(self, username):
        return self.model.all_objects.filter(**{self.model.USERNAME_FIELD: username}).first()


class UnscopedManager(models.Manager):
    """
    Escape hatch: `Model.all_objects` - no tenant filter.
    Use only in admin, platform tooling and tests.
    """

    def get_queryset(self):
        return models.QuerySet(self.model, using=self._db)
