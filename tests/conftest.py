"""
Shared fixtures - for the multi-tenant tests.

Important: TenantManager is strict only during a request
(tenants.managers._tenant_enforcement_active). That is why direct-ORM tests
use `tenant_scope()`, and HTTP tests use the Django test client (the middleware
sets the flag itself).
"""
import contextlib
import datetime as dt

import pytest
from django.utils import timezone

from accounts.models import User
from tenants import context as tenant_ctx
from tenants.models import Hospital


@contextlib.contextmanager
def tenant_scope(hospital):
    """Tenant context + 'request active' flag - for direct ORM tests."""
    prev_hospital = getattr(tenant_ctx._local, "hospital", None)
    prev_request = getattr(tenant_ctx._local, "request_active", False)
    prev_enforce = getattr(tenant_ctx._local, "enforce", True)
    tenant_ctx._local.hospital = hospital
    tenant_ctx._local.request_active = True
    tenant_ctx._local.enforce = True
    try:
        yield hospital
    finally:
        tenant_ctx._local.hospital = prev_hospital
        tenant_ctx._local.request_active = prev_request
        tenant_ctx._local.enforce = prev_enforce


@pytest.fixture
def make_hospital(db):
    counter = {"n": 0}

    def _make(name=None, slug=None, **kwargs):
        counter["n"] += 1
        return Hospital.objects.create(
            name=name or f"Hospital {counter['n']}",
            slug=slug or f"hosp{counter['n']}",
            **kwargs,
        )

    return _make


@pytest.fixture
def hospital_a(make_hospital):
    return make_hospital(name="Acme Hospital", slug="acme")


@pytest.fixture
def hospital_b(make_hospital):
    # NOTE: 'beta' is in RESERVED_SUBDOMAINS, so 'beta-city'
    return make_hospital(name="Beta Hospital", slug="beta-city")


@pytest.fixture
def make_user(db):
    counter = {"n": 0}

    def _make(hospital, username=None, role=User.Role.RECEPTIONIST, password="TestPass!123", **kwargs):
        counter["n"] += 1
        user = User(
            username=username or f"user{counter['n']}",
            hospital=hospital,
            role=role,
            first_name=f"User{counter['n']}",
            **kwargs,
        )
        user.set_password(password)
        user.save()
        return user

    return _make


@pytest.fixture
def make_patient(db):
    counter = {"n": 0}

    def _make(hospital=None, first_name=None, **kwargs):
        from patients.models import Patient

        counter["n"] += 1
        defaults = dict(
            first_name=first_name or f"Patient{counter['n']}",
            last_name="Test",
            date_of_birth=dt.date(1990, 1, 1),
            gender="M",
            phone=f"90000{counter['n']:05d}",
        )
        defaults.update(kwargs)
        with tenant_scope(hospital) if hospital else contextlib.nullcontext():
            return Patient.objects.create(**defaults)

    return _make


@pytest.fixture
def tenant(hospital_a):
    """Run the test in hospital A's context."""
    with tenant_scope(hospital_a):
        yield hospital_a


@pytest.fixture(scope="session", autouse=True)
def _preload_urlconf(django_test_environment):
    """
    Load the URLconf before the tests start (wsgi.py does exactly this in
    production). Otherwise, on the first request, ModelForm's FK default manager
    being tenant-scoped raises ImproperlyConfigured.
    """
    from django.urls import get_resolver

    get_resolver().url_patterns


@pytest.fixture(autouse=True)
def _reset_tenant_context():
    yield
    tenant_ctx.clear_current_hospital()
    tenant_ctx.mark_request_finished()
    # AuditContextMiddleware is thread-local; no stale user on worker reuse
    from core import audit_context

    audit_context.set_current_request(None, None)
