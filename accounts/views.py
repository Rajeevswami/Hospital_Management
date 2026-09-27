from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from core.decorators import role_required
from subscriptions.gating import check_limit
from .forms import StaffCreationForm, LoginForm
from .models import User


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')

    # TenantMiddleware has set request.hospital (from the subdomain or session),
    # and tenants.backends.TenantModelBackend limits authenticate() to that
    # hospital - another hospital's login will not get through here.
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data['username'],
            password=form.cleaned_data['password'],
        )
        if user is not None and user.is_active:
            login(request, user)
            return redirect('dashboard:home')
        messages.error(request, 'Invalid username or password.')
    return render(request, 'accounts/login.html', {
        'form': form,
        'hospital': getattr(request, 'hospital', None),
    })


@login_required
def logout_view(request):
    logout(request)
    return redirect('accounts:login')


@login_required
@role_required('ADMIN')
def staff_list(request):
    # User.objects is tenant-scoped -> only this hospital's staff is visible
    staff = User.objects.exclude(role='ADMIN').order_by('role', 'first_name')
    return render(request, 'accounts/staff_list.html', {
        'staff': staff,
        'hospital': getattr(request, 'hospital', None),
    })


@login_required
@role_required('ADMIN')
def staff_create(request):
    # Phase 2: the plan's staff_limit check
    allowed, reason, limit = check_limit(request, 'staff_limit', User.objects.count())
    if not allowed:
        if reason == 'limit_reached':
            messages.error(request, f'Your plan allows {limit} staff logins. Please upgrade your plan.')
        else:
            messages.error(request, 'An active subscription is required to create staff.')
        return redirect('subscriptions:billing')

    form = StaffCreationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        staff = form.save(commit=False)
        staff.hospital = getattr(request, 'hospital', None) or getattr(request.user, 'hospital', None)
        staff.save()
        messages.success(request, 'Staff account created successfully.')
        return redirect('accounts:staff_list')
    return render(request, 'accounts/staff_form.html', {'form': form})
