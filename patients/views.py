from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.core.paginator import Paginator
from core.decorators import role_required
from subscriptions.gating import check_limit
from .models import Patient
from .forms import PatientForm


@login_required
def patient_list(request):
    query = request.GET.get('q', '').strip()
    patients = Patient.objects.all()
    if query:
        patients = patients.filter(
            Q(patient_id__icontains=query) | Q(first_name__icontains=query) |
            Q(last_name__icontains=query) | Q(phone__icontains=query)
        )
    paginator = Paginator(patients, 15)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'patients/patient_list.html', {'page_obj': page_obj, 'query': query})


@login_required
@role_required('ADMIN', 'RECEPTIONIST')
def patient_create(request):
    # Phase 2: the plan's patient_limit check (nothing happens on unlimited plans)
    allowed, reason, limit = check_limit(request, 'patient_limit', Patient.objects.count())
    if not allowed:
        if reason == 'limit_reached':
            messages.error(request, f'Your plan is limited to {limit} patients. Please upgrade your plan.')
        else:
            messages.error(request, 'An active subscription is required to register a patient.')
        return redirect('subscriptions:billing')

    form = PatientForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        patient = form.save(commit=False)
        patient.registered_by = request.user
        patient.save()
        messages.success(request, f'Patient registered: {patient.patient_id}')
        return redirect('patients:detail', pk=patient.pk)
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Register New Patient'})


@login_required
@role_required('ADMIN', 'RECEPTIONIST')
def patient_update(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(request.POST or None, instance=patient)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Patient details updated.')
        return redirect('patients:detail', pk=patient.pk)
    return render(request, 'patients/patient_form.html', {'form': form, 'title': f'Edit {patient.patient_id}'})


@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    admissions = patient.admissions.select_related('doctor__user', 'bed__ward').all()
    return render(request, 'patients/patient_detail.html', {'patient': patient, 'admissions': admissions})
