from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.forms import ValidationError

from tenants.admin_mixins import TenantAdminMixin
from .models import User


class TenantUserChangeForm(UserChangeForm):
    """
    Username uniqueness ab PER HOSPITAL hai (global unique constraint hata diya),
    isliye admin form ko bhi tenant ke andar validate karna padta hai - warna
    doosre hospital ka same username 'already exists' bol deta.
    """

    class Meta(UserChangeForm.Meta):
        model = User

    def clean_username(self):
        username = self.cleaned_data.get('username')
        hospital = self.cleaned_data.get('hospital') or getattr(self.instance, 'hospital', None)
        qs = User.all_objects.filter(username=username)
        if hospital is not None:
            qs = qs.filter(hospital=hospital)
        if qs.exclude(pk=self.instance.pk).exists():
            raise ValidationError('A user with that username already exists in this hospital.')
        return username


class TenantUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User

    def clean_username(self):
        username = self.cleaned_data.get('username')
        hospital = self.cleaned_data.get('hospital')
        qs = User.all_objects.filter(username=username)
        if hospital is not None:
            qs = qs.filter(hospital=hospital)
        if qs.exists():
            raise ValidationError('A user with that username already exists in this hospital.')
        return username



@admin.register(User)
class CustomUserAdmin(TenantAdminMixin, UserAdmin):
    model = User
    form = TenantUserChangeForm
    add_form = TenantUserCreationForm
    list_display = ('username', 'get_full_name', 'role', 'phone', 'is_active_staff', 'is_active')
    list_filter = ('role', 'is_active')
    search_fields = ('username', 'first_name', 'last_name', 'email')
    fieldsets = UserAdmin.fieldsets + (
        ('Hospital Role', {'fields': ('role', 'phone', 'is_active_staff')}),
        ('Tenant', {'fields': ('hospital', 'is_platform_admin')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Hospital Role', {'fields': ('role', 'phone')}),
        ('Tenant', {'fields': ('hospital', 'is_platform_admin')}),
    )
