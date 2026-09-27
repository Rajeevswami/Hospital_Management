"""
Tenant-aware authentication.

Problem: Django's ModelBackend `authenticate()` does a GLOBAL lookup by
username. In multi-tenant, a hospital B user could log in on hospital A's subdomain -
a path to a data leak.

Fix: TenantModelBackend runs first and looks for the user only within the
current tenant. Backend order (settings.AUTHENTICATION_BACKENDS):

    axes.backends.AxesStandaloneBackend   # lockout check (username based) - first
    tenants.backends.TenantModelBackend   # authenticates within the tenant
    django...ModelBackend                 # fallback (platform super-admin, no tenant)

django-axes lockout still applies per username (AXES_LOCKOUT_PARAMETERS =
['username']) - so brute-force protection works exactly as before.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .context import get_current_hospital


class TenantModelBackend(ModelBackend):
    """Authenticates only the current tenant's users."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        hospital = get_current_hospital()
        if hospital is None:
            # No tenant is active (admin login, management command) -
            # the next backend (ModelBackend) will handle it.
            return None

        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        # Username is unique within the tenant (unique_together hospital+username)
        user = UserModel.all_objects.filter(hospital=hospital, username=username).first()
        if user is None:
            # run the password hash check to reduce the timing side-channel
            UserModel().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def get_user(self, user_id):
        """
        Cross-check the tenant when loading the user from the session.
        If the hospital has changed, treat the session as invalid.
        """
        UserModel = get_user_model()
        try:
            user = UserModel.all_objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None

        hospital = get_current_hospital()
        if hospital is None:
            return user  # platform/admin context
        if getattr(user, "is_platform_admin", False) and user.hospital_id is None:
            return user
        if user.hospital_id != hospital.pk:
            return None  # a user from another tenant - reject the session
        return user


class TenantAwareModelBackend(ModelBackend):
    """
    Django's ModelBackend does a GLOBAL lookup by username. If a tenant is
    active and the user is not in that tenant, this backend would authenticate
    a user from another hospital - a direct data leak.

    So: when a tenant is active -> this backend does nothing (None). When there is no tenant
    (platform super-admin's /admin/login, management command) -> normal behaviour.

    Keep this in settings.AUTHENTICATION_BACKENDS instead of plain ModelBackend.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if get_current_hospital() is not None:
            return None  # the tenant-scoped backend decides
        return super().authenticate(request, username=username, password=password, **kwargs)

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            return UserModel.all_objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
