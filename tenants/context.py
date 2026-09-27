"""
Where the current tenant (Hospital) is kept during a request.

It is set in two places:
  1. tenants.middleware.TenantMiddleware  -> on every HTTP request (from the subdomain)
  2. tenants.context.set_tenant()         -> Celery tasks / management commands / tests

The manager (TenantQuerySet) reads the current tenant from here and limits
every query to that hospital automatically - even if you forget to write a
filter in a view, another hospital's data cannot leak.
"""
import contextlib
import threading

from django.core.exceptions import ImproperlyConfigured

_local = threading.local()


def mark_request_started():
    """TenantMiddleware calls this on every request (also on exempt paths)."""
    _local.request_active = True


def mark_request_finished():
    _local.request_active = False


def is_request_active():
    return getattr(_local, "request_active", False)


def get_current_hospital():
    """Current tenant Hospital instance, or None (if the context is not set)."""
    return getattr(_local, "hospital", None)


def get_current_hospital_id():
    hospital = get_current_hospital()
    return hospital.pk if hospital else None


def set_current_hospital(hospital):
    """Set it explicitly (in a Celery task / management command / test)."""
    _local.hospital = hospital


def clear_current_hospital():
    _local.hospital = None


def is_tenant_enforced():
    """False = tenant checks are skipped (management commands, shell, platform admin tools)."""
    return getattr(_local, "enforce", True)


@contextlib.contextmanager
def tenant_context(hospital, enforce=True):
    """
    with tenant_context(hospital):
        Patient.objects.all()   # only this hospital's patients

    Nested/re-entrant safe - the previous tenant is restored on exit.
    """
    previous_hospital = getattr(_local, "hospital", None)
    previous_enforce = getattr(_local, "enforce", True)
    _local.hospital = hospital
    _local.enforce = enforce
    try:
        yield hospital
    finally:
        _local.hospital = previous_hospital
        _local.enforce = previous_enforce


@contextlib.contextmanager
def all_tenants():
    """
    with all_tenants():
        Patient.objects.all()   # tenant filter OFF (platform admin / reports)

    NOTE: this means a cross-tenant read. Only platform-level code should use it.
    """
    with tenant_context(None, enforce=False):
        yield


def require_current_hospital(model_label=""):
    """
    Call this where a tenant is required. If the context is missing, raise a
    loud error - silently showing all data (or showing empty) are both wrong.
    """
    hospital = get_current_hospital()
    if hospital is None and is_tenant_enforced():
        raise ImproperlyConfigured(
            f"No tenant (Hospital) is active{f' for {model_label}' if model_label else ''}. "
            "Outside a request use `with tenant_context(hospital):`, or do an "
            "explicit cross-tenant read with `with all_tenants():`."
        )
    return hospital
