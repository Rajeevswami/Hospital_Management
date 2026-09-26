"""
Role-based access control used across every app.
Usage on a view:

    @login_required
    @role_required('ADMIN', 'DOCTOR')
    def some_view(request): ...

Or on a class-based view:

    class SomeView(RoleRequiredMixin, View):
        allowed_roles = ['ADMIN', 'DOCTOR']
"""
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if request.user.is_authenticated and request.user.role in roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("You don't have permission to access this page.")
        return _wrapped
    return decorator


class RoleRequiredMixin(LoginRequiredMixin):
    allowed_roles = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if self.allowed_roles and request.user.role not in self.allowed_roles:
            raise PermissionDenied("You don't have permission to access this page.")
        check_tenant(request)
        return super().dispatch(request, *args, **kwargs)


# ====================== MULTI-TENANCY (Phase 1) ======================
# role_required() ka permission logic BILKUL waisa hi hai (koi role rule nahi
# toota). Yeh functions sirf ek extra diwaar add karte hain: user us hospital ka
# hona chahiye jis hospital ka data maanga ja raha hai.


def check_tenant(request):
    """
    User ka hospital == request ka hospital? Nahi to 404 (data ka pata bhi na
    chale). Platform super-admin (hospital=None + is_platform_admin) exempt.
    """
    user = request.user
    if not getattr(user, 'is_authenticated', False):
        return
    if getattr(user, 'is_platform_admin', False) and user.hospital_id is None:
        return
    hospital = getattr(request, 'hospital', None)
    if hospital is None:
        return  # tenant-less context (healthz etc) - middleware already handles
    if user.hospital_id != hospital.pk:
        raise Http404("Not found.")


def tenant_required(view_func):
    """View pe lagao: tenant zaroori hai aur user usi hospital ka hona chahiye."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if getattr(request, 'hospital', None) is None:
            raise PermissionDenied("No hospital context for this request.")
        check_tenant(request)
        return view_func(request, *args, **kwargs)
    return _wrapped


def platform_admin_required(view_func):
    """Sirf SaaS operator (platform super-admin) ke liye - hospital staff ke liye nahi."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not (getattr(user, 'is_authenticated', False) and getattr(user, 'is_platform_admin', False)):
            raise PermissionDenied("Platform administrator access required.")
        return view_func(request, *args, **kwargs)
    return _wrapped
