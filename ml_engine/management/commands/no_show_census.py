"""
PHASE 3 KA PEHLA STEP: kitna historical appointment data available hai?

Yeh command batati hai ki ML model train ho sakta hai ya rule-based fallback
chalega - har hospital ke liye alag.

    python manage.py no_show_census
    python manage.py no_show_census --hospital acme
    python manage.py no_show_census --by-month
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from ml_engine.features import OUTCOME_STATUSES
from ml_engine.predict import readiness
from ml_engine.sklearn_model import get_predictor
from tenants.models import Hospital


class Command(BaseCommand):
    help = "No-show ML ke liye available historical data ka census."

    def add_arguments(self, parser):
        parser.add_argument("--hospital", help="Sirf is slug ka hospital")
        parser.add_argument("--by-month", action="store_true", help="Month-wise breakdown bhi dikhao")

    def handle(self, *args, **opts):
        from appointments.models import Appointment

        hospitals = Hospital.objects.all()
        if opts["hospital"]:
            hospitals = hospitals.filter(slug=opts["hospital"])

        self.stdout.write("")
        self.stdout.write("  NO-SHOW ML DATA CENSUS")
        self.stdout.write("  " + "-" * 68)

        for hospital in hospitals:
            stats = self._stats(Appointment, hospital)
            info = readiness(hospital)
            predictor = get_predictor(hospital)

            self.stdout.write(f"  {hospital.name} (slug={hospital.slug})")
            self.stdout.write(
                f"    total appointments : {stats['total']}"
            )
            self.stdout.write(
                f"    labelled (outcome) : {info['labelled_rows']}  "
                f"[COMPLETED={stats['completed']} NO_SHOW={stats['no_show']} CANCELLED={stats['cancelled']}]"
            )
            self.stdout.write(f"    no-show rate       : {info['positive_rate']:.1%}")
            self.stdout.write(
                f"    ML threshold       : {info['required_rows']} rows "
                f"-> {'READY' if info['ml_ready'] else 'NOT READY'}"
            )
            self.stdout.write(
                f"    active model       : "
                + (predictor.version if predictor else "none (rule-based engine chalega)")
            )
            self.stdout.write(f"    verdict            : {info['verdict']}")

            if opts["by_month"]:
                self._by_month(Appointment, hospital)
            self.stdout.write("")

    def _stats(self, Appointment, hospital):
        qs = Appointment.all_objects.filter(hospital=hospital)
        return {
            "total": qs.count(),
            "completed": qs.filter(status="COMPLETED").count(),
            "no_show": qs.filter(status="NO_SHOW").count(),
            "cancelled": qs.filter(status="CANCELLED").count(),
            "scheduled": qs.filter(status="SCHEDULED").count(),
        }

    def _by_month(self, Appointment, hospital):
        from django.db.models.functions import TruncMonth

        rows = (
            Appointment.all_objects.filter(hospital=hospital, status__in=OUTCOME_STATUSES)
            .annotate(month=TruncMonth("appointment_date"))
            .values("month")
            .annotate(total=Count("id"), no_shows=Count("id", filter=__import__("django.db.models", fromlist=["Q"]).Q(status="NO_SHOW")))
            .order_by("month")
        )
        self.stdout.write("    month-wise:")
        for row in rows:
            month = row["month"].strftime("%Y-%m") if row["month"] else "?"
            rate = (row["no_shows"] / row["total"]) if row["total"] else 0
            self.stdout.write(
                f"      {month}  total={row['total']:<5} no_show={row['no_shows']:<4} ({rate:.0%})"
            )
