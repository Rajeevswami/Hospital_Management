"""
Phase 3 views - no-show risk dashboard.

Poora section `@feature_required("ai_no_show")` se gated hai (abhi sirf Scale
plan mein on). Dashboard ka chhota alert panel bhi usi feature check se dikhta hai.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from appointments.models import Appointment
from core.decorators import role_required
from subscriptions.gating import feature_required

from .models import AppointmentRisk
from .predict import readiness, score_appointment


@login_required
@feature_required("ai_no_show")
def no_show_dashboard(request):
    """High/medium risk upcoming appointments + engine status."""
    from .models import NoShowModelArtifact
    from .sklearn_model import get_predictor

    upcoming = Appointment.objects.filter(
        status=Appointment.Status.SCHEDULED
    ).select_related("patient", "doctor__user", "risk")

    rows = []
    for appt in upcoming:
        risk = getattr(appt, "risk", None)
        if risk is None:
            continue
        rows.append({"appointment": appt, "risk": risk})
    rows.sort(key=lambda r: -r["risk"].score)

    # Platform admin ke paas request.hospital None hota hai - tab global scope
    hospital = getattr(request, "hospital", None)
    predictor = get_predictor(hospital)
    info = readiness(hospital)

    return render(request, "ml_engine/no_show_list.html", {
        "rows": rows[:50],
        "high_count": sum(1 for r in rows if r["risk"].level == AppointmentRisk.Level.HIGH),
        "engine": "sklearn" if predictor else "rules",
        "model_version": predictor.version if predictor else None,
        "readiness": info,
        "active_artifact": NoShowModelArtifact.active_for(hospital),
        "thresholds": {
            "high": _setting("ML_HIGH_RISK_THRESHOLD", 0.65),
            "medium": _setting("ML_MEDIUM_RISK_THRESHOLD", 0.40),
        },
    })


def _setting(name, default):
    from django.conf import settings

    return getattr(settings, name, default)


@login_required
@role_required("ADMIN", "RECEPTIONIST", "DOCTOR")
@feature_required("ai_no_show")
def rescore(request, pk):
    """Ek appointment ka score dobara compute karo (model retrain ke baad kaam aata hai)."""
    appointment = get_object_or_404(Appointment, pk=pk)
    risk = score_appointment(appointment)
    if risk is None:
        messages.error(request, "Score compute nahi ho paya - logs dekho.")
    else:
        messages.success(
            request,
            f"Score {risk.score:.2f} ({risk.get_level_display()}, engine={risk.engine})",
        )
    return redirect("ml_engine:no_show_dashboard")


@login_required
@role_required("ADMIN", "RECEPTIONIST", "DOCTOR")
@feature_required("ai_no_show")
def rescore_all(request):
    """Saare scheduled appointments re-score karo (Celery queue mein jaata hai)."""
    from .tasks import score_appointment_task

    ids = list(
        Appointment.objects.filter(status=Appointment.Status.SCHEDULED)
        .values_list("pk", flat=True)
    )
    for pk in ids:
        score_appointment_task.delay(pk)
    messages.success(request, f"{len(ids)} appointments queue mein daal diye gaye.")
    return redirect("ml_engine:no_show_dashboard")
