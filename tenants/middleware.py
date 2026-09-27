"""
TenantMiddleware - resolves the current Hospital from the request.

Resolution order (TENANCY_MODE=subdomain):
  1. First label of the Host  -> acme.hospitalsaas.in  =>  slug 'acme'
   2. Session fallback     -> the user logged in earlier, now on the root domain
  3. Logged-in user       -> request.user.hospital (direct IP / preview host cases)

After that:
  * request.hospital set
  * tenant context set (so Model.objects scopes itself)
  * data from another tenant requested -> 404
"""
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .context import (
    clear_current_hospital,
    mark_request_finished,
    mark_request_started,
    set_current_hospital,
)
from .models import Hospital

# Tenant is not required on these paths (the login page must open on the root
# domain too, health check, static/media). Prefix match.
DEFAULT_EXEMPT_PREFIXES = (
    "/static/",
    "/media/",
    "/healthz",
)


class TenantMiddleware:
    """MUST be placed after AuthenticationMiddleware (request.user is needed)."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt = tuple(getattr(settings, "TENANT_EXEMPT_PATHS", ())) + DEFAULT_EXEMPT_PREFIXES

    def __call__(self, request):
        clear_current_hospital()
        mark_request_started()
        request.hospital = None

        # ALWAYS resolve the tenant - also on the login page, otherwise
        # authenticate() will not even know which hospital it is.
        hospital, reason = self.resolve(request)
        request.hospital = hospital
        request.tenant_source = reason
        set_current_hospital(hospital)
        # Remember the tenant in the session - also on the login page, otherwise
        # after login there is no way to send the user to their subdomain.
        if hospital is not None:
            self._remember_in_session(request, hospital)
        try:
            if self._is_exempt(request.path):
                # login / admin login / healthz: tenant context is set (needed for
                # auth), but redirect/403 enforcement is skipped.
                return self.get_response(request)
            return self.process(request, hospital)
        finally:
            clear_current_hospital()
            mark_request_finished()

    def _is_exempt(self, path):
        return any(path.startswith(prefix) for prefix in self.exempt)

    # ---------------- resolution ----------------
    def resolve(self, request):
        """(hospital, source) - source is for debugging/logging."""
        mode = getattr(settings, "TENANCY_MODE", "subdomain")

        # Phase 4 (API): a subdomain is not always possible for API clients
        # (Postman/localhost/mobile app). That is why /api/ paths also accept the
        # X-Hospital-Slug header. This is safe because:
        #   * managers are already tenant-scoped
        #   * _guard_cross_tenant matches the user's hospital
        #   * the JWT lookup also goes through the tenant-scoped manager
        if request.path.startswith("/api/"):
            slug = request.headers.get("X-Hospital-Slug")
            if slug:
                hospital = Hospital.objects.filter(slug=slug.strip().lower()).first()
                if hospital:
                    return hospital, "header"

        if mode == "subdomain":
            slug = self.subdomain_from_host(request)
            if slug:
                try:
                    return Hospital.objects.get(slug=slug), "subdomain"
                except Hospital.DoesNotExist:
                    # Unknown subdomain - try the session/user fallback below,
                    # otherwise nothing works on the platform's root domain.
                    pass

        session_key = getattr(settings, "TENANCY_SESSION_KEY", "hospital_slug")
        slug = request.session.get(session_key) if hasattr(request, "session") else None
        if slug:
            hospital = Hospital.objects.filter(slug=slug).first()
            if hospital:
                return hospital, "session"

        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            hospital = getattr(user, "hospital", None)
            if hospital:
                return hospital, "user"

        return None, "none"

    def subdomain_from_host(self, request):
        """acme.example.com + SAAS_ROOT_DOMAIN=example.com  ->  'acme'."""
        root = (getattr(settings, "SAAS_ROOT_DOMAIN", "") or "").strip().lower()
        host = (request.get_host() or "").split(":")[0].strip().lower()
        if not root or not host:
            return None
        if host == root:
            return None
        if host.endswith("." + root):
            label = host[: -(len(root) + 1)]
            # nested subdomains (a.b.example.com) are not supported - one level only
            return label if "." not in label else None
        return None

    # ---------------- enforcement ----------------
    def process(self, request, hospital):
        required = getattr(settings, "TENANT_REQUIRED", True)
        user = getattr(request, "user", None)
        is_authenticated = bool(user is not None and getattr(user, "is_authenticated", False))

        # A platform super-admin (hospital=None) can browse without a tenant
        if is_authenticated and getattr(user, "is_platform_admin", False) and user.hospital_id is None:
            return self.get_response(request)

        if hospital is None:
            if not required:
                return self.get_response(request)
            if self._is_api(request):
                # an HTML login redirect is useless for an API client - send a clean 401
                return self._json_error(
                    request, 401,
                    "Tenant could not be resolved. Use a subdomain "
                    "(acme.example.com/api/...) or send the X-Hospital-Slug header.",
                )
            if is_authenticated:
                # A logged-in user landed on the root domain - send them to their tenant
                user_hospital = getattr(user, "hospital", None)
                if user_hospital and getattr(settings, "TENANCY_MODE", "subdomain") == "subdomain":
                    return redirect(user_hospital.public_url(request) + request.get_full_path())
                raise PermissionDenied("No hospital is associated with your account.")
            # Anonymous + no tenant -> login page (on the root domain)
            return self._login_redirect(request)

        if not hospital.is_active:
            raise PermissionDenied(
                f"{hospital.name} is currently suspended. Please contact support."
            )

        self._remember_in_session(request, hospital)
        blocked = self._guard_cross_tenant(request, user, hospital)
        if blocked is not None:
            return blocked
        return self.get_response(request)

    def _login_redirect(self, request):
        from django.conf import settings as _s

        login_url = getattr(_s, "LOGIN_URL", "/accounts/login/")
        if request.path.startswith(login_url.rstrip("/")):
            return self.get_response(request)
        # NOTE: login_url can be a VIEW NAME ('accounts:login'), so reverse it
        # first and append the query string AFTERWARDS. Calling
        # redirect('accounts:login?next=/x') directly raises DisallowedRedirect.
        target = login_url
        if "://" not in login_url and not login_url.startswith("/"):
            from django.urls import NoReverseMatch, reverse

            try:
                target = reverse(login_url)
            except NoReverseMatch:
                pass
        from urllib.parse import quote

        return redirect(f"{target}?next={quote(request.get_full_path())}")

    # ---------------- Phase 4: API helpers ----------------
    @staticmethod
    def _is_api(request):
        return request.path.startswith("/api/")

    @staticmethod
    def _json_error(request, status, detail):
        from django.http import JsonResponse

        return JsonResponse({"detail": detail}, status=status)

    def _remember_in_session(self, request, hospital):
        session_key = getattr(settings, "TENANCY_SESSION_KEY", "hospital_slug")
        if hasattr(request, "session") and request.session.get(session_key) != hospital.slug:
            request.session[session_key] = hospital.slug

    def _guard_cross_tenant(self, request, user, hospital):
        """
        Defense in depth: if a user belongs to hospital A and sends a request to
        hospital B's subdomain -> 404 (the data's existence is not even revealed).
        Managers are already scoped; this is just an extra wall.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return
        if getattr(user, "is_platform_admin", False):
            return
        user_hospital_id = getattr(user, "hospital_id", None)
        if user_hospital_id is not None and user_hospital_id != hospital.pk:
            # A user from another tenant got here. The managers already stop the
            # data; 403 is here only so the request does not proceed.
            if self._is_api(request):
                return self._json_error(
                    request, 403, "You are signed in to a different hospital."
                )
            raise PermissionDenied("You are signed in to a different hospital.")


def is_exempt_path(path, extra=()):
    """Utility - for checking middleware behaviour in tests."""
    prefixes = tuple(extra) + DEFAULT_EXEMPT_PREFIXES
    return any(path.startswith(p) for p in prefixes)
