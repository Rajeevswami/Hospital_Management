"""
API-specific DRF exceptions.

Reason: gating ka jawab 403 nahi, **402 Payment Required** hona chahiye -
client ko pata chale ki yeh permission ka issue nahi, PLAN ka issue hai.
HTML side (subscriptions.gating._deny_response) bhi yahi status use karta hai.
"""
from rest_framework.exceptions import APIException


class ApiFeatureDenied(APIException):
    status_code = 402
    default_detail = "Yeh API aapke current plan mein nahi hai."
    default_code = "feature_not_in_plan"

    def __init__(self, feature=None, reason=None, detail=None):
        self.feature = feature
        self.reason = reason
        super().__init__(detail or self.default_detail)


class NoTenant(APIException):
    status_code = 400
    default_detail = (
        "Tenant resolve nahi hua. Subdomain use karo (acme.example.com/api/...) "
        "ya X-Hospital-Slug header bhejo."
    )
    default_code = "no_tenant"


def api_exception_handler(exc, context):
    """
    DRF ka default handler + do zaroori additions:

    1. Django ki PermissionDenied / ValidationError (jo model.clean() phenkta hai)
       ko DRF-style JSON mein badlo. Warna client ko HTML 500/403 milta.
    2. Tenant na ho -> 400 with clear hint (subdomain / header).
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
        # Gating ke 402 responses mein reason/feature bhi bhejo - client ko
        # "kyun nahi mila" pata chale (HTML side ka behaviour mirror karta hai)
        if isinstance(exc, ApiFeatureDenied):
            response.data = {
                "detail": str(exc.detail),
                "reason": exc.reason,
                "feature": exc.feature,
            }
        # Har error mein code bhi, taaki client string match na kare
        if isinstance(response.data, dict) and "code" not in response.data:
            code = getattr(exc, "default_code", None) or getattr(exc, "code", None)
            if code:
                response.data["code"] = str(code)
    return response
