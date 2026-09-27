"""
DRF permission classes - multi-tenant + RBAC + plan gating.

Three different things are checked, deliberately in separate classes:
  1. IsTenantMember   - the user belongs to this hospital (stop cross-tenant leaks)
  2. HasRole          - the existing RBAC (the DRF version of role_required)
  3. HasApiAccess     - the plan has the `api_access` feature (Phase 2 gating)

`Model.objects` is already tenant-scoped; these permissions are defense-in-depth.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS

from subscriptions.gating import check_feature, is_platform_admin
from subscriptions.models import Feature


class IsTenantMember(BasePermission):
    """
    request.user's hospital == request's tenant? If not, 403.
    Platform admin (hospital=None + is_platform_admin) exempt.
    """

    message = "You are not staff of this hospital."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if is_platform_admin(request):
            return True
        hospital = getattr(request, "hospital", None)
        if hospital is None:
            return False
        return user.hospital_id == hospital.pk


def has_role(*roles):
    """
    Class factory:  permission_classes = [has_role("ADMIN", "RECEPTIONIST")]
    The exact behaviour of the existing `core.decorators.role_required` - the role rule
    has not changed at all; it is only wrapped for DRF.
    """
    allowed = set(roles)

    class _HasRole(BasePermission):
        message = "This action is not allowed for your role."

        def has_permission(self, request, view):
            user = getattr(request, "user", None)
            if user is None or not getattr(user, "is_authenticated", False):
                return False
            if is_platform_admin(request):
                return True
            return getattr(user, "role", None) in allowed

    _HasRole.__name__ = f"HasRole_{'_'.join(roles)}"
    _HasRole.allowed_roles = tuple(roles)
    return _HasRole


class ReadOnlyUnlessWriteRoles(BasePermission):
    """
    Read for all roles, write only for the given roles.
    (The Patients/Appointments API needs exactly this pattern.)
    """

    write_roles = ()
    message = "This action is not allowed for your role."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if request.method in SAFE_METHODS:
            return True
        if is_platform_admin(request):
            return True
        return getattr(user, "role", None) in set(self.write_roles)


class HasApiAccess(BasePermission):
    """
    If the plan lacks the `api_access` feature -> 402 (not 403) - with the
    reason, so the client knows to upgrade, not to fix permissions.
    """

    def has_permission(self, request, view):
        from .exceptions import ApiFeatureDenied

        allowed, reason = check_feature(request, Feature.API_ACCESS)
        if not allowed:
            raise ApiFeatureDenied(feature=Feature.API_ACCESS, reason=reason)
        return True
