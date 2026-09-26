"""
Feature gating (Phase 2).

Usage:
    @login_required
    @feature_required("ai_no_show")
    def some_view(request): ...

    class SomeView(FeatureRequiredMixin, View):
        required_feature = "api_access"

Rules:
  * Subscription hi nahi        -> DENY (message ke saath upgrade page pe bhejo)
  * Subscription inactive/expired -> DENY
  * Plan mein feature off        -> DENY
  * Platform super-admin         -> ALLOW (SaaS operator ko testing ke liye)

Gating ka source of truth `Subscription.for_hospital(request.hospital)` hai -
DB se padhta hai, cache nahi, isliye plan badalte hi turant asar hota hai.
"""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


class FeatureDenied(PermissionDenied):
    """Plan mein yeh feature nahi hai (ya subscription active nahi)."""

    def __init__(self, message="Your current plan does not include this feature.", **kwargs):
        super().__init__(message)
        self.__dict__.update(kwargs)


def get_subscription(request):
    """Request ke tenant ki subscription (ya None)."""
    from .models import Subscription

    hospital = getattr(request, "hospital", None)
    return Subscription.for_hospital(hospital)


def is_platform_admin(request):
    user = getattr(request, "user", None)
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_platform_admin", False)
    )


def check_feature(request, feature):
    """
    Returns (allowed: bool, reason: str).
    Reason UI/messages ke liye hai - "kyun nahi mila" batana zaroori hai.
    """
    if is_platform_admin(request):
        return True, "platform admin"

    sub = get_subscription(request)
    if sub is None:
        return False, "no_subscription"
    if not sub.is_accessible:
        return False, f"subscription_{sub.status.lower()}"
    if not sub.plan.has_feature(feature):
        return False, "not_in_plan"
    return True, "allowed"


def check_limit(request, limit_attr, current_count):
    """
    (allowed, reason, limit_value) - plan ke numeric limit ke liye.
    limit_attr: 'patient_limit' | 'staff_limit' | 'appointment_limit'
    """
    from .models import UNLIMITED

    if is_platform_admin(request):
        return True, "platform admin", UNLIMITED

    sub = get_subscription(request)
    if sub is None or not sub.is_accessible:
        return False, "no_active_subscription", 0

    limit = getattr(sub.plan, limit_attr, UNLIMITED)
    if limit == UNLIMITED or limit < 0:
        return True, "unlimited", limit
    if current_count >= limit:
        return False, "limit_reached", limit
    return True, "under_limit", limit


def _deny_response(request, feature, reason):
    """402 Payment Required + friendly message + billing page pe redirect."""
    friendly = {
        "no_subscription": "Aapke hospital ki koi active subscription nahi hai.",
        "subscription_cancelled": "Subscription cancel ho chuki hai.",
        "subscription_expired": "Subscription expire ho gayi hai.",
        "subscription_halted": "Payment failures ki wajah se subscription halted hai.",
        "subscription_past_due": "Payment pending hai - kripya payment complete karo.",
        "not_in_plan": f"Yeh feature ({feature}) aapke current plan mein nahi hai.",
    }.get(reason, "Yeh feature abhi available nahi hai.")

    if getattr(request, "accepts_html", True) and not request.path.startswith("/api/"):
        messages.warning(request, f"{friendly} Plan upgrade karo.")
        return redirect("subscriptions:billing")
    from django.http import JsonResponse

    return JsonResponse(
        {"detail": friendly, "reason": reason, "feature": feature}, status=402
    )


def feature_required(feature):
    """View decorator - plan mein feature na ho to 402 / upgrade redirect."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            allowed, reason = check_feature(request, feature)
            if not allowed:
                return _deny_response(request, feature, reason)
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


class FeatureRequiredMixin:
    """CBV ke liye: `required_feature = "api_access"`"""

    required_feature = None

    def dispatch(self, request, *args, **kwargs):
        feature = self.required_feature
        if feature:
            allowed, reason = check_feature(request, feature)
            if not allowed:
                return _deny_response(request, feature, reason)
        return super().dispatch(request, *args, **kwargs)


class LimitRequiredMixin:
    """
    Create-views ke liye: plan ka numeric limit check.
        class PatientCreate(LimitRequiredMixin, ...):
            limit_attr = "patient_limit"
            def count_existing(self, request): return Patient.objects.count()
    """

    limit_attr = None

    def count_existing(self, request):  # pragma: no cover - subclass override karta hai
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        if self.limit_attr:
            allowed, reason, limit = check_limit(request, self.limit_attr, self.count_existing(request))
            if not allowed:
                if reason == "limit_reached":
                    messages.error(
                        request,
                        f"Aapke plan ki limit {limit} hai - pehle plan upgrade karo.",
                    )
                    return redirect("subscriptions:billing")
                return _deny_response(request, self.limit_attr, reason)
        return super().dispatch(request, *args, **kwargs)
