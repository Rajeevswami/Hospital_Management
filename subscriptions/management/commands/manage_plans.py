"""
Seed/update the plan catalog.

    python manage.py manage_plans              # default 4 plans create/update
    python manage.py manage_plans --list       # just show them
    python manage.py manage_plans --ensure-razorpay   # create the plans on Razorpay

It is idempotent - updates based on the code, does not create duplicates.
"""
import json

from django.core.management.base import BaseCommand
from django.db import transaction

from subscriptions import razorpay_service as rzp
from subscriptions.models import Feature, Plan

# (code, name, price, patient_limit, staff_limit, features, sort)
DEFAULT_PLANS = [
    ("free", "Free", 0, 50, 2, {}, 0),
    ("starter", "Starter", 999, 300, 5, {Feature.ADVANCED_REPORTS: True}, 1),
    ("growth", "Growth", 2499, 1500, 20, {
        Feature.ADVANCED_REPORTS: True,
        Feature.SMS_REMINDERS: True,
        Feature.CUSTOM_BRANDING: True,
    }, 2),
    ("scale", "Scale", 4999, -1, 100, {  # -1 = unlimited patients
        Feature.AI_NO_SHOW: True,
        Feature.API_ACCESS: True,
        Feature.SMS_REMINDERS: True,
        Feature.CUSTOM_BRANDING: True,
        Feature.MULTI_BRANCH: True,
        Feature.ADVANCED_REPORTS: True,
    }, 3),
]

DESCRIPTIONS = {
    "free": "For a trial - 50 patients, 2 staff, core features.",
    "starter": "For a small clinic - with reports.",
    "growth": "For a growing hospital - SMS reminders + branding.",
    "scale": "Multi-branch + AI no-show prediction + REST API.",
}


class Command(BaseCommand):
    help = "Seed/update the default SaaS plans."

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="Only show the plans")
        parser.add_argument("--ensure-razorpay", action="store_true",
                            help="Create a plan on Razorpay for every plan (plan_id will be stored)")
        parser.add_argument("--only", help="Only the plan with this code")

    def handle(self, *args, **opts):
        if opts["list"]:
            for plan in Plan.objects.all():
                feats = [k for k, v in plan.features.items() if v]
                self.stdout.write(
                    f"  {plan.code:<10} Rs.{plan.price:<8} patients={plan.patient_limit:<6} "
                    f"staff={plan.staff_limit:<5} features={feats or '-'} "
                    f"razorpay={plan.razorpay_plan_id or '-'}"
                )
            return

        with transaction.atomic():
            for code, name, price, plimit, slimit, features, order in DEFAULT_PLANS:
                if opts["only"] and opts["only"] != code:
                    continue
                plan, created = Plan.objects.update_or_create(
                    code=code,
                    defaults={
                        "name": name,
                        "price": price,
                        "patient_limit": plimit,
                        "staff_limit": slimit,
                        "features_json": features,
                        "description": DESCRIPTIONS.get(code, ""),
                        "sort_order": order,
                        "is_active": True,
                    },
                )
                self.stdout.write(f"  {'CREATED' if created else 'UPDATED'} {plan.code}")

        if opts["ensure_razorpay"]:
            if not rzp.is_configured():
                self.stderr.write(self.style.ERROR(
                    "Razorpay keys are not set - put RAZORPAY_KEY_ID/SECRET in .env."
                ))
                return
            for plan in Plan.objects.active().exclude(price=0):
                try:
                    plan_id = rzp.ensure_plan(plan)
                    self.stdout.write(f"  razorpay plan: {plan.code} -> {plan_id}")
                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f"  {plan.code}: {exc}"))
