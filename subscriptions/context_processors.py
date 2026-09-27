"""
Subscription/plan info for templates - available on every request.
With it, the navbar/dashboard can show the "current plan" and feature-gated buttons.
"""
from .gating import check_feature, get_subscription, is_platform_admin
from .models import Feature


def subscription(request):
    sub = get_subscription(request)
    platform = is_platform_admin(request)
    accessible = platform or bool(sub and sub.is_accessible)

    def has(feature):
        if platform:
            return True
        allowed, _reason = check_feature(request, feature)
        return allowed

    # Templates use `{% if feature_flags.ai_no_show %}`.
    # It is built from the already-fetched subscription - no extra DB query.
    feature_flags = {
        feature: (True if platform else bool(accessible and sub.plan.has_feature(feature)))
        for feature in Feature.values
    }

    return {
        "current_subscription": sub,
        "current_plan": sub.plan if sub else None,
        "subscription_accessible": accessible,
        "has_feature": has,
        "feature_flags": feature_flags,
        "is_platform_admin": platform,
        "FEATURES": Feature,
    }
