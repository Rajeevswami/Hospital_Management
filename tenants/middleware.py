"""
TenantMiddleware - request se current Hospital resolve karta hai.

Resolution order (TENANCY_MODE=subdomain):
  1. Host ka pehla label  -> acme.hospitalsaas.in  =>  slug 'acme'
  2. Session fallback     -> user ne pehle login kiya tha, ab root domain pe aaya
  3. Logged-in user       -> request.user.hospital (direct IP / preview host cases)

Iske baad:
  * request.hospital set
  * tenant context set (taaki Model.objects khud scope ho jaaye)
  * doosre tenant ka data maanga -> 404
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

# In paths pe tenant zaroori nahi (login page root domain pe bhi khulna chahiye,
# health check, static/media). Prefix match hota hai.
DEFAULT_EXEMPT_PREFIXES = (
    "/static/",
    "/media/",
    "/healthz",
)


class TenantMiddleware:
    """MUST be placed after AuthenticationMiddleware (request.user chahiye)."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt = tuple(getattr(settings, "TENANT_EXEMPT_PATHS", ())) + DEFAULT_EXEMPT_PREFIXES

    def __call__(self, request):
        clear_current_hospital()
        mark_request_started()
        request.hospital = None

        # Tenant HAMESHA resolve karo - login page pe bhi chahiye, warna
        # authenticate() ko pata hi nahi chalega ki kaunsa hospital hai.
        hospital, reason = self.resolve(request)
        request.hospital = hospital
        request.tenant_source = reason
        set_current_hospital(hospital)
        # Tenant ko session mein yaad rakho - login page pe bhi, warna login ke
        # baad user ko apne subdomain pe bhejne ka koi zariya nahi bachta.
        if hospital is not None:
            self._remember_in_session(request, hospital)
        try:
            if self._is_exempt(request.path):
                # login / admin login / healthz: tenant context set hai (auth ke
                # liye zaroori), par redirect/403 enforcement skip.
                return self.get_response(request)
            return self.process(request, hospital)
        finally:
            clear_current_hospital()
            mark_request_finished()

    def _is_exempt(self, path):
        return any(path.startswith(prefix) for prefix in self.exempt)

    # ---------------- resolution ----------------
    def resolve(self, request):
        """(hospital, source) - source debug/logging ke liye."""
        mode = getattr(settings, "TENANCY_MODE", "subdomain")

        # Phase 4 (API): API clients ke liye subdomain hamesha possible nahi hota
        # (Postman/localhost/mobile app). Isliye /api/ paths pe X-Hospital-Slug
        # header bhi accept hota hai. Yeh safe hai kyunki:
        #   * managers already tenant-scoped hain
        #   * _guard_cross_tenant user ka hospital match karta hai
        #   * JWT lookup bhi tenant-scoped manager se hota hai
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
                    # Unknown subdomain - neeche session/user fallback try karo,
                    # warna platform ke root domain pe kaam nahi chalega.
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
            # nested subdomain (a.b.example.com) support nahi - sirf ek level
            return label if "." not in label else None
        return None

    # ---------------- enforcement ----------------
    def process(self, request, hospital):
        required = getattr(settings, "TENANT_REQUIRED", True)
        user = getattr(request, "user", None)
        is_authenticated = bool(user is not None and getattr(user, "is_authenticated", False))

        # Platform super-admin (hospital=None) tenant ke bina bhi ghoom sakta hai
        if is_authenticated and getattr(user, "is_platform_admin", False) and user.hospital_id is None:
            return self.get_response(request)

        if hospital is None:
            if not required:
                return self.get_response(request)
            if self._is_api(request):
                # HTML login redirect API client ke liye bekaar hai - saaf 401 bhejo
                return self._json_error(
                    request, 401,
                    "Tenant resolve nahi hua. Subdomain use karo "
                    "(acme.example.com/api/...) ya X-Hospital-Slug header bhejo.",
                )
            if is_authenticated:
                # Logged-in user root domain pe aa gaya - uske tenant pe bhej do
                user_hospital = getattr(user, "hospital", None)
                if user_hospital and getattr(settings, "TENANCY_MODE", "subdomain") == "subdomain":
                    return redirect(user_hospital.public_url(request) + request.get_full_path())
                raise PermissionDenied("No hospital is associated with your account.")
            # Anonymous + no tenant -> login page (root domain pe)
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
        # NOTE: login_url ek VIEW NAME ho sakta hai ('accounts:login'), isliye
        # pehle reverse karo aur query string BAAD mein jodo. Seedha
        # redirect('accounts:login?next=/x') dene se DisallowedRedirect aata hai.
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
        Defense in depth: agar koi user hospital A ka hai aur hospital B ke
        subdomain pe request bhej de -> 404 (data ka pata bhi na chale).
        Managers already scoped hain; yeh sirf extra diwaar hai.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return
        if getattr(user, "is_platform_admin", False):
            return
        user_hospital_id = getattr(user, "hospital_id", None)
        if user_hospital_id is not None and user_hospital_id != hospital.pk:
            # Doosre tenant ka user yahan aa gaya. Data to managers already rok
            # rahe hain; yahan sirf request aage na badhe isliye 403.
            if self._is_api(request):
                return self._json_error(
                    request, 403, "You are signed in to a different hospital."
                )
            raise PermissionDenied("You are signed in to a different hospital.")


def is_exempt_path(path, extra=()):
    """Utility - tests mein middleware behaviour check karne ke liye."""
    prefixes = tuple(extra) + DEFAULT_EXEMPT_PREFIXES
    return any(path.startswith(p) for p in prefixes)
