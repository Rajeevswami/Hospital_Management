"""
Razorpay recurring-subscription integration (Phase 2).

All keys come from .env (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET /
RAZORPAY_WEBHOOK_SECRET) - nothing is hardcoded in the code.

Two things to keep in mind:
  1. Webhook signature verification is done OURSELVES with HMAC-SHA256 (no SDK
     dependency) - because this is a security boundary and must be testable.
  2. If the SDK is not installed or the keys are not set -> a clear error, not a
     crash - otherwise local dev / a tenant without Razorpay would take the
     whole app down.
"""
import hashlib
import hmac
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class RazorpayNotConfigured(Exception):
    """Keys missing or SDK not installed - show this message to the user."""


# Razorpay subscription entity status -> our Subscription.Status
STATUS_MAP = {
    "created": "PENDING",
    "authenticated": "ACTIVE",
    "active": "ACTIVE",
    "pending": "PENDING",
    "halted": "HALTED",
    "cancelled": "CANCELLED",
    "completed": "EXPIRED",
    "expired": "EXPIRED",
    "paused": "PAST_DUE",
}


def get_keys():
    """(key_id, key_secret, webhook_secret) - from settings, never hardcoded."""
    key_id = getattr(settings, "RAZORPAY_KEY_ID", "") or ""
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "") or ""
    webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "") or ""
    return key_id, key_secret, webhook_secret


def is_configured():
    key_id, key_secret, _ = get_keys()
    return bool(key_id and key_secret)


def require_configured():
    if not is_configured():
        raise RazorpayNotConfigured(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET in .env (start with test-mode keys)."
        )


def get_client():
    """Razorpay SDK client. Handles both ImportError and missing keys."""
    require_configured()
    try:
        import razorpay
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RazorpayNotConfigured(
            "the razorpay package is not installed: pip install -r requirements.txt"
        ) from exc
    key_id, key_secret, _ = get_keys()
    return razorpay.Client(auth=(key_id, key_secret))


# ----------------------------------------------------------------- signatures
def _hmac_sha256_hex(secret, payload):
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_webhook_signature(raw_body, signature):
    """
    Razorpay webhook signature check.

    raw_body must be the EXACT bytes that arrived in the request (request.body) -
    parsing JSON and dumping it again will not match the signature.
    A timing-safe compare is used.
    """
    _, _, webhook_secret = get_keys()
    if not webhook_secret:
        raise RazorpayNotConfigured(
            "RAZORPAY_WEBHOOK_SECRET is not set - cannot verify the webhook."
        )
    if not signature:
        return False
    expected = _hmac_sha256_hex(webhook_secret, raw_body)
    return hmac.compare_digest(expected, str(signature))


def verify_checkout_signature(razorpay_subscription_id, razorpay_payment_id, signature):
    """
    Verify the signature that came from the browser after checkout.
    expected = HMAC_SHA256(key_secret, "{subscription_id}|{payment_id}")
    """
    require_configured()
    if not (razorpay_subscription_id and razorpay_payment_id and signature):
        return False
    _, key_secret, _ = get_keys()
    payload = f"{razorpay_subscription_id}|{razorpay_payment_id}"
    return hmac.compare_digest(_hmac_sha256_hex(key_secret, payload), str(signature))


# ------------------------------------------------------------------- entities
def ensure_plan(plan):
    """
    Create the plan on Razorpay for this Plan (unless plan_id already exists).
    Idempotent: if razorpay_plan_id is set, that same one is reused.
    """
    if plan.razorpay_plan_id:
        return plan.razorpay_plan_id
    client = get_client()
    period = 12 if plan.interval == plan.Interval.YEARLY else 1
    data = {
        "period": "yearly" if plan.interval == plan.Interval.YEARLY else "monthly",
        "interval": period,
        "item": {
            "name": plan.name,
            "description": plan.description or f"{plan.name} subscription",
            "amount": plan.price_paise,
            "currency": plan.currency or "INR",
        },
        "notes": {"plan_code": plan.code},
    }
    created = client.plan.create(data=data)
    plan.razorpay_plan_id = created["id"]
    plan.save(update_fields=["razorpay_plan_id", "updated_at"])
    logger.info("Razorpay plan created: %s -> %s", plan.code, plan.razorpay_plan_id)
    return plan.razorpay_plan_id


def create_customer(hospital):
    client = get_client()
    data = {
        "name": hospital.name,
        "email": hospital.contact_email or f"{hospital.slug}@tenant.local",
        "contact": hospital.contact_phone or "0000000000",
        "notes": {"hospital_slug": hospital.slug},
    }
    return client.customer.create(data=data)


def create_subscription(subscription):
    """
    Create the Razorpay subscription entity and store the ids on the local Subscription.
    The customer for the hospital already exists (or is created now).
    """
    client = get_client()
    plan = subscription.plan

    rzp_plan_id = ensure_plan(plan)
    customer_id = subscription.razorpay_customer_id
    if not customer_id and subscription.hospital_id:
        customer_id = create_customer(subscription.hospital)["id"]
        subscription.razorpay_customer_id = customer_id

    data = {
        "plan_id": rzp_plan_id,
        "total_count": 12 if plan.interval == plan.Interval.YEARLY else 12,
        "customer_notify": 1,
        "notes": {
            "hospital_id": str(subscription.hospital_id or ""),
            "subscription_id": str(subscription.pk or ""),
        },
    }
    if customer_id:
        data["customer_id"] = customer_id

    created = client.subscription.create(data=data)
    subscription.razorpay_subscription_id = created["id"]
    subscription.razorpay_plan_id = rzp_plan_id
    subscription.status = STATUS_MAP.get(created.get("status"), subscription.Status.PENDING)
    subscription.save(
        update_fields=[
            "razorpay_subscription_id", "razorpay_plan_id",
            "razorpay_customer_id", "status", "updated_at",
        ]
    )
    return created


def fetch_subscription(razorpay_subscription_id):
    return get_client().subscription.fetch(razorpay_subscription_id)


def cancel_subscription(razorpay_subscription_id, cancel_at_cycle_end=True):
    return get_client().subscription.cancel(
        razorpay_subscription_id, {"cancel_at_cycle_end": cancel_at_cycle_end}
    )


def checkout_payload(subscription):
    """
    Data needed for the Razorpay.js checkout. The template embeds it as JSON
    and passes it to the handler script.
    """
    require_configured()
    key_id, _, _ = get_keys()
    hospital = subscription.hospital
    return {
        "key": key_id,
        "subscription_id": subscription.razorpay_subscription_id,
        "name": (hospital.name if hospital else "Hospital") + " - " + subscription.plan.name,
        "description": f"{subscription.plan.name} subscription",
        "customer_id": subscription.razorpay_customer_id or None,
        "prefill": {
            "email": (hospital.contact_email if hospital else "") or "",
            "contact": (hospital.contact_phone if hospital else "") or "",
        },
        "notes": {"hospital": hospital.slug if hospital else ""},
        "theme": {"color": "#0d6efd"},
    }
