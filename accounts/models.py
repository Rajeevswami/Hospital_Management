from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models

from tenants.managers import TenantManager, UnscopedManager


class TenantUserManager(TenantManager, UserManager):
    """
    Default manager: tenant-scoped queryset + Django's UserManager helpers.

    `UserManager` is required because `AbstractUser.clean()` internally calls
    `self.__class__.objects.normalize_email(self.email)`. A plain
    `TenantManager` (built from `models.Manager`) does not have that method at
    all, so any UserCreationForm (Staff → Add) returned a 500 on save:
        AttributeError: 'TenantManager' object has no attribute 'normalize_email'
    """

    # `UserManager.use_in_migrations = True` is inherited - keep it OFF,
    # otherwise `makemigrations` keeps asking for AlterModelManagers. Tenant
    # managers depend on the runtime tenant context; serializing them into
    # migrations is simply wrong.
    use_in_migrations = False


class UnscopedUserManager(UnscopedManager, UserManager):
    """`User.all_objects` - without tenant filtering, but with UserManager helpers."""

    use_in_migrations = False


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

    # MULTI-TENANT (Phase 1): username is no longer GLOBALLY unique. Overriding
    # AbstractUser's unique=True was necessary, otherwise two hospitals could not
    # share the 'admin' username. Per-hospital uniqueness comes from the
    # constraint below.
    username = models.CharField(
        'username', max_length=150, unique=False,
        validators=[UnicodeUsernameValidator()],
        help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.',
        error_messages={'unique': 'A user with that username already exists.'},
    )

    # ---------------- MULTI-TENANCY (Phase 1) ----------------
    # hospital is NULL only for platform super-admins (who run the SaaS,
    # not staff of any one hospital). Normal staff always have a hospital set.
    hospital = models.ForeignKey(
        'tenants.Hospital', on_delete=models.CASCADE, null=True, blank=True,
        related_name='staff',
        help_text='Which hospital this login belongs to. NULL = platform super-admin.',
    )
    is_platform_admin = models.BooleanField(
        default=False,
        help_text='Platform (SaaS) operator - can see all hospitals. OFF for hospital staff.',
    )

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.RECEPTIONIST)
    phone = models.CharField(max_length=15, blank=True)
    is_active_staff = models.BooleanField(default=True, help_text="Deactivate instead of deleting accounts")

    # objects -> the current tenant's staff; all_objects -> everything (auth backend / platform admin)
    objects = TenantUserManager()
    all_objects = UnscopedUserManager()
    # an unscoped manager for Django's own internals (createsuperuser, contrib.auth)
    unscoped = UserManager()

    class Meta:
        # Username is no longer GLOBALLY unique - each hospital can have its own 'admin'.
        # Email is also unique per-hospital (the same email in two hospitals can be different logins).
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
        """Tenant check helper - for views/decorators."""
        if self.is_platform_admin and self.hospital_id is None:
            return True
        return bool(hospital) and self.hospital_id == hospital.pk
