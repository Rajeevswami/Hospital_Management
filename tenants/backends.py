"""
Tenant-aware authentication.

Problem: `authenticate()` Django ka ModelBackend username se GLOBAL lookup karta
hai. Multi-tenant mein hospital B ka user hospital A ke subdomain pe login kar
sakta - data leak ka raasta.

Fix: TenantModelBackend pehle chalta hai aur current tenant ke andar hi user
dhundhta hai. Backend order (settings.AUTHENTICATION_BACKENDS):

    axes.backends.AxesStandaloneBackend   # lockout check (username based) - sabse pehle
    tenants.backends.TenantModelBackend   # tenant ke andar authenticate
    django...ModelBackend                 # fallback (platform super-admin, koi tenant nahi)

django-axes ka lockout abhi bhi username pe lagta hai (AXES_LOCKOUT_PARAMETERS =
['username']) - isliye brute-force protection bilkul waisi hi kaam karti hai.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .context import get_current_hospital


class TenantModelBackend(ModelBackend):
    """Sirf current tenant ke users ko authenticate karta hai."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        hospital = get_current_hospital()
        if hospital is None:
            # Koi tenant active nahi (admin login, management command) -
            # aage ModelBackend dekhega.
            return None

        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        # Tenant ke andar username unique hai (unique_together hospital+username)
        user = UserModel.all_objects.filter(hospital=hospital, username=username).first()
        if user is None:
            # timing side-channel kam karne ke liye password hash check chalao
            UserModel().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def get_user(self, user_id):
        """
        Session se user load karte waqt tenant cross-check.
        Hospital switch ho gaya ho to session invalid maano.
        """
        UserModel = get_user_model()
        try:
            user = UserModel.all_objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None

        hospital = get_current_hospital()
        if hospital is None:
            return user  # platform/admin context
        if getattr(user, "is_platform_admin", False) and user.hospital_id is None:
            return user
        if user.hospital_id != hospital.pk:
            return None  # doosre tenant ka user - session reject
        return user


class TenantAwareModelBackend(ModelBackend):
    """
    Django ka ModelBackend username se GLOBAL lookup karta hai. Agar tenant active
    ho aur user us tenant mein na mile, to yeh backend doosre hospital ka user
    authenticate kar deta - seedha data leak.

    Isliye: tenant active ho -> yeh backend kuch nahi karta (None). Tenant na ho
    (platform super-admin ka /admin/login, management command) -> normal behaviour.

    settings.AUTHENTICATION_BACKENDS mein isi ko rakho, plain ModelBackend ki jagah.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if get_current_hospital() is not None:
            return None  # tenant-scoped backend hi decide karega
        return super().authenticate(request, username=username, password=password, **kwargs)

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            return UserModel.all_objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
