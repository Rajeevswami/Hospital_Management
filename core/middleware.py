import time
from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.contrib import messages
from . import audit_context


class AuditContextMiddleware:
    """Captures who's making the request so signal handlers can log it (see audit_context.py)."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR')
        user = request.user if request.user.is_authenticated else None
        audit_context.set_current_request(user, ip)
        try:
            return self.get_response(request)
        finally:
            # So a stale user/IP cannot leak on thread reuse (gunicorn worker) -
            # otherwise the next request could write an audit log under another user's name.
            audit_context.set_current_request(None, None)


class IdleSessionTimeoutMiddleware:
    """
    Auto-logs out staff after IDLE_TIMEOUT_SECONDS of no activity.
    Important for hospital terminals - shared computers at the reception desk
    should not stay logged in indefinitely if someone walks away.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = getattr(settings, 'IDLE_TIMEOUT_SECONDS', 1800)

    def __call__(self, request):
        if request.user.is_authenticated:
            last_activity = request.session.get('last_activity')
            now = time.time()
            if last_activity and (now - last_activity) > self.timeout:
                logout(request)
                messages.info(request, "You were logged out due to inactivity. Please log in again.")
                return redirect('accounts:login')
            request.session['last_activity'] = now
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Extra hardening headers beyond Django's built-in security middleware."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['Referrer-Policy'] = 'same-origin'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        return response
