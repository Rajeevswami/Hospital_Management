"""
Sync subscription status from Razorpay.

If a webhook is missed (server down, network), this command does the
reconciliation. Put it on cron:
    */15 * * * * python manage.py sync_subscriptions
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from subscriptions import razorpay_service as rzp
from subscriptions.models import Subscription


class Command(BaseCommand):
    help = "Sync the status of pending/active subscriptions from Razorpay."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="All accessible subscriptions (default: only non-terminal)")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        if not rzp.is_configured():
            self.stderr.write(self.style.ERROR("Razorpay is not configured."))
            return

        qs = Subscription.all_objects.all() if opts["all"] else Subscription.all_objects.filter(
            status__in=[Subscription.Status.PENDING, Subscription.Status.ACTIVE,
                        Subscription.Status.PAST_DUE, Subscription.Status.TRIALING]
        ).exclude(razorpay_subscription_id="")

        count = 0
        for sub in qs:
            if not sub.razorpay_subscription_id:
                continue
            try:
                data = rzp.fetch_subscription(sub.razorpay_subscription_id)
            except Exception as exc:
                self.stderr.write(f"  {sub.razorpay_subscription_id}: {exc}")
                continue
            new_status = rzp.STATUS_MAP.get(data.get("status"), sub.status)
            changed = new_status != sub.status
            if changed and not opts["dry_run"]:
                sub.status = new_status
                sub.save(update_fields=["status", "updated_at"])
            if changed:
                count += 1
                self.stdout.write(f"  {sub.razorpay_subscription_id}: {sub.status} -> {new_status}")

        self.stdout.write(self.style.SUCCESS(
            f"{count} subscription(s) {'would change' if opts['dry_run'] else 'updated'} "
            f"(checked {qs.count()}) at {timezone.now():%H:%M:%S}"
        ))
