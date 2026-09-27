"""
Phase 2 views: billing/upgrade page, Razorpay checkout, webhook.

The webhook is at /saas/webhook/ and must be in TENANT_EXEMPT_PATHS -
a Razorpay request has no subdomain/session, and it is verified by the
signature (not by a tenant).
"""
import json
import logging
from datetime import datetime as dt
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.decorators import platform_admin_required, role_required
from tenants.models import Hospital

from . import razorpay_service as rzp
from .gating import get_subscription
from .models import PaymentEvent, Plan, Subscription

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- billing UI
@login_required
def billing(request):
    """Current plan + usage + upgrade options."""
    from accounts.models import User
    from patients.models import Patient

    hospital = getattr(request, "hospital", None)
    sub = get_subscription(request)

    usage = {}
    if hospital is not None:
        usage = {
            "patients": Patient.all_objects.filter(hospital=hospital).count(),
            "staff": User.all_objects.filter(hospital=hospital).count(),
        }

    return render(request, "subscriptions/billing.html", {
        "subscription": sub,
        "plans": Plan.objects.active(),
        "usage": usage,
        "razorpay_configured": rzp.is_configured(),
        "grace_days": getattr(settings, "SUBSCRIPTION_GRACE_DAYS", 3),
    })


@login_required
@role_required("ADMIN")
def start_checkout(request, plan_code):
    """Create the Razorpay subscription and send the user to the checkout page."""
    hospital = getattr(request, "hospital", None)
    if hospital is None and not getattr(request.user, "is_platform_admin", False):
        messages.error(request, "No hospital context found.")
        return redirect("subscriptions:billing")

    plan = get_object_or_404(Plan, code=plan_code, is_active=True)
    sub, _created = Subscription.all_objects.get_or_create(
        hospital=hospital, defaults={"plan": plan, "status": Subscription.Status.PENDING}
    )
    if sub.plan_id != plan.pk:
        sub.plan = plan
        sub.save(update_fields=["plan", "updated_at"])

    try:
        rzp.create_subscription(sub)
        payload = rzp.checkout_payload(sub)
    except rzp.RazorpayNotConfigured as exc:
        messages.error(request, str(exc))
        return redirect("subscriptions:billing")
    except Exception as exc:  # network / API error
        logger.exception("Razorpay subscription create failed")
        messages.error(request, f"Razorpay error: {exc}")
        return redirect("subscriptions:billing")

    return render(request, "subscriptions/checkout.html", {
        "subscription": sub, "checkout": payload,
    })


@login_required
@role_required("ADMIN")
@require_POST
def checkout_callback(request):
    """Verify the signature from the browser, then activate the subscription."""
    sub_id = request.POST.get("razorpay_subscription_id")
    payment_id = request.POST.get("razorpay_payment_id")
    signature = request.POST.get("razorpay_signature")

    if not rzp.verify_checkout_signature(sub_id, payment_id, signature):
        messages.error(request, "Payment signature could not be verified - please contact support.")
        return redirect("subscriptions:billing")

    sub = Subscription.all_objects.filter(razorpay_subscription_id=sub_id).first()
    if sub is None:
        messages.error(request, "Subscription record not found.")
        return redirect("subscriptions:billing")

    period_end = timezone.now() + timezone.timedelta(days=30)
    sub.mark_active(period_end=period_end, payment_id=payment_id)
    messages.success(request, f"Subscription active: {sub.plan.name}")
    return redirect("subscriptions:billing")


@platform_admin_required
def cancel(request):
    """A platform admin cancels a tenant's subscription."""
    sub = get_object_or_404(Subscription, pk=request.GET.get("id"))
    sub.status = Subscription.Status.CANCELLED
    sub.cancelled_at = timezone.now()
    sub.save(update_fields=["status", "cancelled_at", "updated_at"])
    messages.success(request, f"Subscription {sub.pk} cancelled.")
    return redirect("subscriptions:billing")


# ------------------------------------------------------------------ webhook
WEBHOOK_HANDLERS = {}


def _register(*event_types):
    def deco(fn):
        for ev in event_types:
            WEBHOOK_HANDLERS[ev] = fn
        return fn
    return deco


@_register("subscription.authenticated", "subscription.activated", "subscription.resumed")
def _on_activated(event, sub, entity):
    period_end = entity.get("current_end")
    sub.mark_active(period_end=_epoch_to_dt(period_end))


@_register("subscription.charged")
def _on_charged(event, sub, entity):
    charge_at = entity.get("charge_at") or entity.get("end_time")
    sub.mark_active(period_end=_epoch_to_dt(charge_at) if charge_at else None)
    payment = entity.get("payment_id")
    if payment:
        sub.last_payment_id = payment
        sub.save(update_fields=["last_payment_id", "updated_at"])


@_register("payment.failed")
def _on_payment_failed(event, sub, entity):
    sub.mark_payment_failed()


@_register("subscription.halted")
def _on_halted(event, sub, entity):
    sub.status = Subscription.Status.HALTED
    sub.save(update_fields=["status", "updated_at"])


@_register("subscription.cancelled")
def _on_cancelled(event, sub, entity):
    sub.status = Subscription.Status.CANCELLED
    sub.cancelled_at = timezone.now()
    sub.save(update_fields=["status", "cancelled_at", "updated_at"])


@_register("subscription.completed", "subscription.expired")
def _on_expired(event, sub, entity):
    sub.status = Subscription.Status.EXPIRED
    sub.save(update_fields=["status", "updated_at"])


@_register("subscription.paused")
def _on_paused(event, sub, entity):
    sub.status = Subscription.Status.PAST_DUE
    sub.save(update_fields=["status", "updated_at"])


def _epoch_to_dt(value):
    """Razorpay epoch seconds -> aware datetime.
    NOTE: `django.utils.timezone.utc` was removed in Django 5.0, so we use
    `datetime.timezone.utc`."""
    if not value:
        return None
    try:
        return dt.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """
    Razorpay webhook endpoint.

    Order matters:
      1. verify the signature against the raw body (first) - on failure 400, nothing is processed
      2. write PaymentEvent (event_id unique -> duplicate delivery ignored)
      3. Handler chalao
    """
    raw_body = request.body
    signature = request.headers.get("X-Razorpay-Signature", "")

    try:
        if not rzp.verify_webhook_signature(raw_body, signature):
            logger.warning("Razorpay webhook signature mismatch")
            return JsonResponse({"error": "invalid signature"}, status=400)
    except rzp.RazorpayNotConfigured as exc:
        return JsonResponse({"error": str(exc)}, status=503)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid json"}, status=400)

    event_id = payload.get("event_id") or payload.get("id") or ""
    event_type = payload.get("event") or ""
    entity = ((payload.get("payload") or {}).get("subscription") or {}).get("entity") or {}

    with transaction.atomic():
        event, created = PaymentEvent.objects.get_or_create(
            event_id=event_id or f"unknown-{timezone.now().timestamp()}",
            defaults={"event_type": event_type, "payload": payload},
        )
        if not created:
            # Razorpay can send the same event again - do not process it twice
            return JsonResponse({"status": "duplicate, ignored"}, status=200)

        sub = None
        rzp_sub_id = entity.get("id")
        if rzp_sub_id:
            sub = Subscription.all_objects.filter(razorpay_subscription_id=rzp_sub_id).first()
        if sub is None:
            notes = entity.get("notes") or {}
            if notes.get("subscription_id"):
                sub = Subscription.all_objects.filter(pk=notes["subscription_id"]).first()

        event.subscription = sub
        handler = WEBHOOK_HANDLERS.get(event_type)
        if handler is None:
            event.processed = True
            event.error = "no handler for event type"
            event.save(update_fields=["processed", "error", "subscription"])
            return JsonResponse({"status": "ignored"}, status=200)
        if sub is None:
            event.processed = False
            event.error = "subscription not found"
            event.save(update_fields=["processed", "error"])
            return JsonResponse({"status": "subscription not found"}, status=404)

        try:
            handler(event, sub, entity)
            event.processed = True
            event.save(update_fields=["processed", "subscription"])
        except Exception as exc:
            logger.exception("webhook handler failed for %s", event_type)
            event.processed = False
            event.error = str(exc)[:255]
            event.save(update_fields=["processed", "error", "subscription"])
            return JsonResponse({"error": "handler failed"}, status=500)

    return JsonResponse({"status": "ok"}, status=200)


@csrf_exempt
def webhook_probe(request):
    """Health: whether the webhook URL is reachable (no signature check on GET)."""
    return HttpResponse("ok", content_type="text/plain")
