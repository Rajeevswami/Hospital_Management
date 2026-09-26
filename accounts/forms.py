from django import forms
from django.contrib.auth.forms import UserCreationForm
from tenants.context import get_current_hospital
from .models import User


class StaffCreationForm(UserCreationForm):
    """Used by Admin to create new staff logins (doctor, receptionist, pharmacist)."""

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'role']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_username(self):
        """
        Username uniqueness ab PER HOSPITAL hai (do hospitals dono ka 'admin' ho
        sakta hai). Isliye global check ki jagah current tenant ke andar check.
        """
        username = self.cleaned_data['username']
        qs = User.all_objects.filter(username=username)
        hospital = get_current_hospital()
        if hospital is not None:
            qs = qs.filter(hospital=hospital)
        if qs.exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(
                'A user with that username already exists in this hospital.'
            )
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.hospital_id is None:
            user.hospital = get_current_hospital()
        if commit:
            user.save()
            self.save_m2m()
        return user


class LoginForm(forms.Form):
    username = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control', 'autofocus': True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control'}))
