"""
DRF permission classes - multi-tenant + RBAC + plan gating.

Teen alag-alag cheezein check hoti hain, jaan-boojh ke alag classes mein:
  1. IsTenantMember   - user isi hospital ka hai (cross-tenant leak rokna)
  2. HasRole          - existing RBAC (role_required ka DRF version)
  3. HasApiAccess     - plan mein `api_access` feature hai (Phase 2 gating)

`Model.objects` already tenant-scoped hai; yeh permissions defense-in-depth hain.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS

from subscriptions.gating import check_feature, is_platform_admin
from subscriptions.models import Feature


class IsTenantMember(BasePermission):
    """
    request.user ka hospital == request ka tenant? Nahi to 403.
    Platform admin (hospital=None + is_platform_admin) exempt.
    """

    message = "Aap is hospital ke staff nahi ho."

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
    Existing `core.decorators.role_required` ka exact behaviour - role rule
    bilkul nahi badla, sirf DRF ke liye wrap kiya gaya hai.
    """
    allowed = set(roles)

    class _HasRole(BasePermission):
        message = "Yeh action aapke role ke liye allowed nahi hai."

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
    Padhna sab roles ko, likhna sirf diye gaye roles ko.
    (Patients/Appointments API mein yahi pattern chahiye.)
    """

    write_roles = ()
    message = "Yeh action aapke role ke liye allowed nahi hai."

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
    Plan mein `api_access` feature na ho to 402 (403 nahi) - reason ke saath,
    taaki client ko pata chale ki upgrade karna hai, permission fix nahi karni.
    """

    def has_permission(self, request, view):
        from .exceptions import ApiFeatureDenied

        allowed, reason = check_feature(request, Feature.API_ACCESS)
        if not allowed:
            raise ApiFeatureDenied(feature=Feature.API_ACCESS, reason=reason)
        return True
