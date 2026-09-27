"""
API-specific DRF exceptions.

Reason: the gating response must be **402 Payment Required**, not 403 -
so the client knows this is not a permission issue, it is a PLAN issue.
The HTML side (subscriptions.gating._deny_response) uses the same status.
"""
from rest_framework.exceptions import APIException


class ApiFeatureDenied(APIException):
    status_code = 402
    default_detail = "This API is not included in your current plan."
    default_code = "feature_not_in_plan"

    def __init__(self, feature=None, reason=None, detail=None):
        self.feature = feature
        self.reason = reason
        super().__init__(detail or self.default_detail)


class NoTenant(APIException):
    status_code = 400
    default_detail = (
        "Tenant could not be resolved. Use a subdomain (acme.example.com/api/...) "
        "or send the X-Hospital-Slug header."
    )
    default_code = "no_tenant"


def api_exception_handler(exc, context):
    """
    DRF's default handler + two necessary additions:

    1. Convert Django's PermissionDenied / ValidationError (raised by
       model.clean()) to DRF-style JSON. Otherwise the client gets HTML 500/403.
    2. No tenant -> 400 with a clear hint (subdomain / header).
    """
    from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework.response import Response
    from rest_framework.views import exception_handler

    if isinstance(exc, DjangoPermissionDenied):
        from rest_framework.exceptions import PermissionDenied

        exc = PermissionDenied(detail=str(exc))
    elif isinstance(exc, DjangoValidationError):
        from rest_framework.exceptions import ValidationError

        exc = ValidationError(detail=getattr(exc, "message_dict", None) or exc.messages)

    response = exception_handler(exc, context)

    if response is not None:
        # Also send reason/feature in gating 402 responses - so the client
        # knows "why it did not get it" (mirrors the HTML side behaviour)
        if isinstance(exc, ApiFeatureDenied):
            response.data = {
                "detail": str(exc.detail),
                "reason": exc.reason,
                "feature": exc.feature,
            }
        # Include a code in every error, so the client does not string-match
        if isinstance(response.data, dict) and "code" not in response.data:
            code = getattr(exc, "default_code", None) or getattr(exc, "code", None)
            if code:
                response.data["code"] = str(code)
    return response
