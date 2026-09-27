"""
Feature gating (Phase 2).

Usage:
    @login_required
    @feature_required("ai_no_show")
    def some_view(request): ...

    class SomeView(FeatureRequiredMixin, View):
        required_feature = "api_access"

Rules:
  * No subscription at all      -> DENY (send to the upgrade page with a message)
  * Subscription inactive/expired -> DENY
  * Feature off in the plan      -> DENY
  * Platform super-admin         -> ALLOW (so the SaaS operator can test)

The source of truth for gating is `Subscription.for_hospital(request.hospital)` -
it reads from the DB, not a cache, so a plan change takes effect immediately.
"""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


class FeatureDenied(PermissionDenied):
    """This feature is not in the plan (or the subscription is not active)."""

    def __init__(self, message="Your current plan does not include this feature.", **kwargs):
        super().__init__(message)
        self.__dict__.update(kwargs)


def get_subscription(request):
    """The request's tenant's subscription (or None)."""
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
    The reason is for the UI/messages - it is important to explain "why it was not granted".
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
    (allowed, reason, limit_value) - for the plan's numeric limit.
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
    """402 Payment Required + friendly message + redirect to the billing page."""
    friendly = {
        "no_subscription": "Your hospital has no active subscription.",
        "subscription_cancelled": "The subscription has been cancelled.",
        "subscription_expired": "The subscription has expired.",
        "subscription_halted": "The subscription is halted due to payment failures.",
        "subscription_past_due": "Payment is pending - please complete the payment.",
        "not_in_plan": f"This feature ({feature}) is not in your current plan.",
    }.get(reason, "This feature is not available right now.")

    if getattr(request, "accepts_html", True) and not request.path.startswith("/api/"):
        messages.warning(request, f"{friendly} Please upgrade your plan.")
        return redirect("subscriptions:billing")
    from django.http import JsonResponse

    return JsonResponse(
        {"detail": friendly, "reason": reason, "feature": feature}, status=402
    )


def feature_required(feature):
    """View decorator - if the plan lacks the feature -> 402 / upgrade redirect."""

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
    """For CBVs: `required_feature = "api_access"`"""

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
    For create-views: the plan's numeric limit check.
        class PatientCreate(LimitRequiredMixin, ...):
            limit_attr = "patient_limit"
            def count_existing(self, request): return Patient.objects.count()
    """

    limit_attr = None

    def count_existing(self, request):  # pragma: no cover - overridden by the subclass
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        if self.limit_attr:
            allowed, reason, limit = check_limit(request, self.limit_attr, self.count_existing(request))
            if not allowed:
                if reason == "limit_reached":
                    messages.error(
                        request,
                        f"Your plan's limit is {limit} - please upgrade your plan first.",
                    )
                    return redirect("subscriptions:billing")
                return _deny_response(request, self.limit_attr, reason)
        return super().dispatch(request, *args, **kwargs)
