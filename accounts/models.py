from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models

from tenants.managers import TenantManager, UnscopedManager


class User(AbstractUser):
    """
    Custom user model with hospital role baked in.
    Every staff login (admin, doctor, receptionist, pharmacist) is one User row.
    Role drives what menus/permissions they see across the whole app.
    """

    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        DOCTOR = 'DOCTOR', 'Doctor'
        RECEPTIONIST = 'RECEPTIONIST', 'Receptionist'
        PHARMACIST = 'PHARMACIST', 'Pharmacist'

    # MULTI-TENANT (Phase 1): username ab GLOBALLY unique nahi. AbstractUser ka
    # unique=True override karna zaroori tha, warna do hospitals 'admin' username
    # share nahi kar sakte the. Per-hospital uniqueness neeche constraint se aati hai.
    username = models.CharField(
        'username', max_length=150, unique=False,
        validators=[UnicodeUsernameValidator()],
        help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.',
        error_messages={'unique': 'A user with that username already exists.'},
    )

    # ---------------- MULTI-TENANCY (Phase 1) ----------------
    # hospital NULL sirf platform super-admin ke liye hota hai (jo SaaS chalate hain,
    # kisi ek hospital ke staff nahi). Normal staff ka hospital hamesha set hota hai.
    hospital = models.ForeignKey(
        'tenants.Hospital', on_delete=models.CASCADE, null=True, blank=True,
        related_name='staff',
        help_text='Yeh login kis hospital ka hai. NULL = platform super-admin.',
    )
    is_platform_admin = models.BooleanField(
        default=False,
        help_text='Platform (SaaS) operator - sab hospitals dekh sakta hai. Hospital staff ke liye OFF.',
    )

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.RECEPTIONIST)
    phone = models.CharField(max_length=15, blank=True)
    is_active_staff = models.BooleanField(default=True, help_text="Deactivate instead of deleting accounts")

    # objects -> current tenant ke staff; all_objects -> sab (auth backend / platform admin)
    objects = TenantManager()
    all_objects = UnscopedManager()
    # Django ke apne internals (createsuperuser, contrib.auth) ke liye unscoped manager
    unscoped = UserManager()

    class Meta:
        # Username ab GLOBALLY unique nahi - har hospital ka apna 'admin' ho sakta hai.
        # Email bhi per-hospital unique (do hospitals mein same email alag logins ho sakte hain).
        constraints = [
            models.UniqueConstraint(fields=['hospital', 'username'], name='uniq_username_per_hospital'),
            models.UniqueConstraint(
                fields=['hospital', 'email'], name='uniq_email_per_hospital',
                condition=models.Q(email__gt=''),
            ),
        ]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_doctor(self):
        return self.role == self.Role.DOCTOR

    @property
    def is_receptionist(self):
        return self.role == self.Role.RECEPTIONIST

    @property
    def is_pharmacist(self):
        return self.role == self.Role.PHARMACIST

    @property
    def hospital_name(self):
        return self.hospital.name if self.hospital_id else 'Platform'

    def belongs_to(self, hospital):
        """Tenant check helper - views/decorators ke liye."""
        if self.is_platform_admin and self.hospital_id is None:
            return True
        return bool(hospital) and self.hospital_id == hospital.pk
