from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import timedelta

from patients.models import Patient
from doctors.models import Doctor
from wards.models import Admission, Bed, Ward
from appointments.models import Appointment
from pharmacy.models import Medicine, Prescription
from billing.models import Invoice, Payment

from subscriptions.gating import check_feature


@login_required
def home(request):
    user = request.user
    today = timezone.now().date()
    context = {}

    if user.is_admin:
        context.update(_admin_stats(today))
    elif user.is_doctor:
        context.update(_doctor_stats(user, today))
    elif user.is_receptionist:
        context.update(_receptionist_stats(today))
    elif user.is_pharmacist:
        context.update(_pharmacist_stats())

    # Phase 3: AI no-show alerts - sirf jab plan mein ai_no_show on ho.
    # Doctor ko sirf apne appointments dikhte hain (existing RBAC ke hisaab se).
    context['ai_no_show_enabled'], _reason = check_feature(request, 'ai_no_show')
    if context['ai_no_show_enabled']:
        context['high_risk_appointments'] = _high_risk_appointments(user)

    return render(request, 'dashboard/home.html', context)


def _high_risk_appointments(user):
    from ml_engine.models import AppointmentRisk

    risks = AppointmentRisk.high_risk_upcoming()
    doctor = getattr(user, 'doctor_profile', None)
    if user.is_doctor and doctor is not None:
        risks = [r for r in risks if r.appointment.doctor_id == doctor.pk]
    return risks


def _admin_stats(today):
    total_revenue = Payment.objects.aggregate(total=Sum('amount'))['total'] or 0
    pending_dues = sum(inv.balance_due for inv in Invoice.objects.exclude(status='CANCELLED'))

    # Revenue trend for last 7 days, for the Chart.js line chart
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    revenue_by_day = []
    for d in days:
        total = Payment.objects.filter(paid_at__date=d).aggregate(total=Sum('amount'))['total'] or 0
        revenue_by_day.append(float(total))

    total_beds = Bed.objects.count()
    occupied_beds = Bed.objects.filter(is_occupied=True).count()

    return {
        'role_view': 'admin',
        'total_patients': Patient.objects.count(),
        'total_doctors': Doctor.objects.filter(is_active=True).count(),
        'currently_admitted': Admission.objects.filter(status='ADMITTED').count(),
        'todays_appointments': Appointment.objects.filter(appointment_date=today).count(),
        'total_revenue': total_revenue,
        'pending_dues': pending_dues,
        'total_beds': total_beds,
        'occupied_beds': occupied_beds,
        'free_beds': total_beds - occupied_beds,
        'occupancy_pct': round((occupied_beds / total_beds * 100), 1) if total_beds else 0,
        'low_stock_count': sum(1 for m in Medicine.objects.all() if m.is_low_stock),
        'chart_labels': [d.strftime('%d %b') for d in days],
        'chart_revenue': revenue_by_day,
    }


def _doctor_stats(user, today):
    doctor = getattr(user, 'doctor_profile', None)
    todays_appts = Appointment.objects.filter(doctor=doctor, appointment_date=today).order_by('appointment_time') if doctor else []
    return {
        'role_view': 'doctor',
        'todays_appointments_list': todays_appts,
        'my_total_patients': Appointment.objects.filter(doctor=doctor).values('patient').distinct().count() if doctor else 0,
        'my_admitted_patients': Admission.objects.filter(doctor=doctor, status='ADMITTED').count() if doctor else 0,
    }


def _receptionist_stats(today):
    return {
        'role_view': 'receptionist',
        'todays_appointments': Appointment.objects.filter(appointment_date=today).count(),
        'todays_admissions': Admission.objects.filter(admission_date__date=today).count(),
        'currently_admitted': Admission.objects.filter(status='ADMITTED').count(),
        'free_beds': Bed.objects.filter(is_occupied=False).count(),
    }


def _pharmacist_stats():
    low_stock = [m for m in Medicine.objects.all() if m.is_low_stock]
    return {
        'role_view': 'pharmacist',
        'low_stock_medicines': low_stock,
        'total_medicines': Medicine.objects.count(),
        'todays_prescriptions': Prescription.objects.filter(created_at__date=timezone.now().date()).count(),
    }
