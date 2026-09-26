"""
Existing appointments ke risk score compute karo (bulk).

    python manage.py score_appointments                     # sirf SCHEDULED
    python manage.py score_appointments --all               # sab status
    python manage.py score_appointments --hospital acme
    python manage.py score_appointments --async             # Celery queue mein daalo

Pehli baar setup ke baad yeh chalao, warna purane appointments ka score nahi hoga
(signal sirf NAYE appointment pe chalta hai).
"""
from django.core.management.base import BaseCommand

from ml_engine.tasks import score_appointment_task
from tenants.context import tenant_context
from tenants.models import Hospital


class Command(BaseCommand):
    help = "Appointments ke no-show risk score compute karo."

    def add_arguments(self, parser):
        parser.add_argument("--hospital", help="Sirf is slug ka hospital")
        parser.add_argument("--all", action="store_true", help="SCHEDULED ke alawa sab status")
        parser.add_argument("--async", dest="use_async", action="store_true",
                            help="Celery queue mein daalo (worker chalna chahiye)")
        parser.add_argument("--limit", type=int, help="Sirf itne appointments")

    def handle(self, *args, **opts):
        from appointments.models import Appointment

        hospitals = Hospital.objects.all()
        if opts["hospital"]:
            hospitals = hospitals.filter(slug=opts["hospital"])

        total, scored = 0, 0
        for hospital in hospitals:
            qs = Appointment.all_objects.filter(hospital=hospital).select_related(
                "patient", "doctor", "hospital"
            )
            if not opts["all"]:
                qs = qs.filter(status=Appointment.Status.SCHEDULED)
            if opts["limit"]:
                qs = qs[: opts["limit"]]

            ids = list(qs.values_list("pk", flat=True))
            total += len(ids)

            with tenant_context(hospital):
                for pk in ids:
                    if opts["use_async"]:
                        score_appointment_task.delay(pk)
                        scored += 1
                    else:
                        from ml_engine.predict import score_appointment

                        appt = Appointment.all_objects.get(pk=pk)
                        if score_appointment(appt) is not None:
                            scored += 1
            self.stdout.write(f"  {hospital.slug}: {len(ids)} appointments")

        mode = "queued" if opts["use_async"] else "scored"
        self.stdout.write(self.style.SUCCESS(f"{scored}/{total} appointments {mode}."))
