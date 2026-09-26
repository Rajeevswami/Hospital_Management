"""
Razorpay recurring-subscription integration (Phase 2).

Saare keys .env se aate hain (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET /
RAZORPAY_WEBHOOK_SECRET) - code mein kuch hardcode nahi hai.

Do baatein dhyan mein rakhna:
  1. Webhook signature verification HMAC-SHA256 se KHUD ki gayi hai (SDK pe
     depend nahi karte) - kyunki yeh security boundary hai aur testable honi chahiye.
  2. SDK na installed ho ya keys set na hon to clear error, crash nahi -
     warna local dev / bina Razorpay wale tenant pe poora app down ho jaata.
"""
import hashlib
import hmac
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class RazorpayNotConfigured(Exception):
    """Keys missing ya SDK installed nahi - user ko yeh message dikhao."""


# Razorpay subscription entity status -> apna Subscription.Status
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
    """(key_id, key_secret, webhook_secret) - settings se, kabhi hardcode nahi."""
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
            "Razorpay configured nahi hai. .env mein RAZORPAY_KEY_ID aur "
            "RAZORPAY_KEY_SECRET set karo (test-mode keys se shuru karo)."
        )


def get_client():
    """Razorpay SDK client. ImportError/keys dono handle karta hai."""
    require_configured()
    try:
        import razorpay
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RazorpayNotConfigured(
            "razorpay package installed nahi hai: pip install -r requirements.txt"
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

    raw_body EXACT bytes hone chahiye jo request mein aaye (request.body) -
    JSON parse karke dobara dumps karne se signature match nahi hoga.
    Timing-safe compare use hota hai.
    """
    _, _, webhook_secret = get_keys()
    if not webhook_secret:
        raise RazorpayNotConfigured(
            "RAZORPAY_WEBHOOK_SECRET set nahi hai - webhook verify nahi kar sakte."
        )
    if not signature:
        return False
    expected = _hmac_sha256_hex(webhook_secret, raw_body)
    return hmac.compare_digest(expected, str(signature))


def verify_checkout_signature(razorpay_subscription_id, razorpay_payment_id, signature):
    """
    Checkout ke baad browser se aaya hua signature verify karo.
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
    Plan ke liye Razorpay pe plan banao (agar plan_id pehle se nahi hai).
    Idempotent: razorpay_plan_id set hai to wapas wahi use hota hai.
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
    Razorpay subscription entity banao aur local Subscription pe ids store karo.
    Hospital ke liye customer pehle se bana hoga (ya abhi ban jaayega).
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
    Razorpay.js checkout ke liye zaroori data. Template isko JSON mein daal ke
    handler script ko deta hai.
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
