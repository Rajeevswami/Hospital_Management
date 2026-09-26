"""
Templates mein subscription/plan info - har request pe available.
Isse navbar/dashboard pe "current plan" aur feature-gated buttons dikh sakte hain.
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

    # Templates mein `{% if feature_flags.ai_no_show %}` use hota hai.
    # Yeh usi already-fetched subscription se banta hai - extra DB query nahi.
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
