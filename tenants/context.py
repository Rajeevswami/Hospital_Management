"""
Current tenant (Hospital) ko request ke dauraan kahan rakha jaata hai.

Do jagah set hota hai:
  1. tenants.middleware.TenantMiddleware  -> har HTTP request pe (subdomain se)
  2. tenants.context.set_tenant()         -> Celery tasks / management commands / tests

Manager (TenantQuerySet) yahin se current tenant padhta hai aur har query
automatically usi hospital tak limit kar deta hai - views mein filter likhna
bhool jaao to bhi doosre hospital ka data leak nahi hoga.
"""
import contextlib
import threading

from django.core.exceptions import ImproperlyConfigured

_local = threading.local()


def mark_request_started():
    """TenantMiddleware har request pe call karta hai (exempt paths pe bhi)."""
    _local.request_active = True


def mark_request_finished():
    _local.request_active = False


def is_request_active():
    return getattr(_local, "request_active", False)


def get_current_hospital():
    """Current tenant Hospital instance, ya None (agar context set nahi hua)."""
    return getattr(_local, "hospital", None)


def get_current_hospital_id():
    hospital = get_current_hospital()
    return hospital.pk if hospital else None


def set_current_hospital(hospital):
    """Explicitly set karo (Celery task / management command / test mein)."""
    _local.hospital = hospital


def clear_current_hospital():
    _local.hospital = None


def is_tenant_enforced():
    """False = tenant check skip (management commands, shell, platform admin tools)."""
    return getattr(_local, "enforce", True)


@contextlib.contextmanager
def tenant_context(hospital, enforce=True):
    """
    with tenant_context(hospital):
        Patient.objects.all()   # sirf isi hospital ke patients

    Nested/re-entrant safe - bahar nikalte hi purana tenant wapas aa jaata hai.
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

    NOTE: iska matlab cross-tenant read hai. Sirf platform-level code use kare.
    """
    with tenant_context(None, enforce=False):
        yield


def require_current_hospital(model_label=""):
    """
    Tenant zaroori ho wahan call karo. Context missing ho to loud error -
    chup-chaap saara data dikha dena (ya khaali dikha dena) dono galat hain.
    """
    hospital = get_current_hospital()
    if hospital is None and is_tenant_enforced():
        raise ImproperlyConfigured(
            f"No tenant (Hospital) is active{f' for {model_label}' if model_label else ''}. "
            "Request ke bahar `with tenant_context(hospital):` use karo, ya "
            "`with all_tenants():` se explicitly cross-tenant read karo."
        )
    return hospital
