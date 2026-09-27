"""
Assign a plan to a hospital (create/replace the subscription).

    python manage.py assign_plan --hospital acme --plan growth
    python manage.py assign_plan --hospital acme --plan growth --days 30
    python manage.py assign_plan --hospital acme --plan free --trial-days 14

For local testing and manual onboarding. No Razorpay involvement -
so this command works without keys too.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from subscriptions.models import Plan, Subscription
from tenants.models import Hospital


class Command(BaseCommand):
    help = "Assign a plan to a hospital (activate its subscription)."

    def add_arguments(self, parser):
        parser.add_argument("--hospital", required=True, help="Hospital slug or id")
        parser.add_argument("--plan", required=True, help="Plan code")
        parser.add_argument("--days", type=int, default=30, help="Billing period length (default 30)")
        parser.add_argument("--trial-days", type=int, default=0, help="Trial period (status=TRIALING)")

    @transaction.atomic
    def handle(self, *args, **opts):
        try:
            hospital = Hospital.objects.get(slug=opts["hospital"])
        except Hospital.DoesNotExist:
            try:
                hospital = Hospital.objects.get(pk=int(opts["hospital"]))
            except (Hospital.DoesNotExist, ValueError):
                raise CommandError(f"Hospital not found: {opts['hospital']}")

        try:
            plan = Plan.objects.get(code=opts["plan"])
        except Plan.DoesNotExist:
            raise CommandError(
                f"Plan not found: {opts['plan']}. Run `manage.py manage_plans` first."
            )

        now = timezone.now()
        trial_days = opts["trial_days"]
        status = Subscription.Status.TRIALING if trial_days else Subscription.Status.ACTIVE
        period_end = now + timezone.timedelta(days=trial_days or opts["days"])

        sub, created = Subscription.all_objects.update_or_create(
            hospital=hospital,
            defaults={
                "plan": plan,
                "status": status,
                "current_period_start": now,
                "current_period_end": period_end,
                "trial_ends_at": now + timezone.timedelta(days=trial_days) if trial_days else None,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'CREATED' if created else 'UPDATED'}: {hospital.name} -> {plan.name} "
            f"({sub.status}, valid till {period_end:%Y-%m-%d})"
        ))
