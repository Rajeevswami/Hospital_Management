"""
Tenant model - one Hospital = one customer = one isolated data set.

Subdomain scheme (chosen in Phase 1):
    acme.hospitalsaas.in  ->  Hospital(slug='acme')
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.text import slugify

from . import context
from .managers import TenantManager, UnscopedManager


class HospitalManager(models.Manager):
    def active(self):
        return self.get_queryset().filter(is_active=True)


class Hospital(models.Model):
    """
    NOTE: Hospital is not itself a tenant (its own manager is not
    tenant-scoped), otherwise "which hospital exists" could not be resolved.
    """

    name = models.CharField(max_length=150, help_text="Display name of the hospital")
    slug = models.SlugField(
        max_length=63, unique=True, db_index=True,
        help_text="Subdomain - 'acme' for acme.example.com. Lowercase, no spaces.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="False = subscription lapse / suspended. Login is blocked, data stays safe.",
    )
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=15, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = HospitalManager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        slug = slugify(self.slug or self.name)
        if not slug:
            raise ValidationError("Slug or name is required.")
        if slug in RESERVED_SUBDOMAINS:
            raise ValidationError(
                f"'{slug}' is reserved (for the platform's own pages). Choose another slug."
            )

    def save(self, *args, **kwargs):
        # slug normalize + reserved check on every save (whether created from the shell or a form)
        self.clean()
        self.slug = slugify(self.slug or self.name)
        super().save(*args, **kwargs)

    # ---------- helpers ----------
    @property
    def subdomain(self):
        return self.slug

    def public_url(self, request=None):
        """https://<slug>.<SAAS_ROOT_DOMAIN>"""
        root = getattr(settings, "SAAS_ROOT_DOMAIN", "localhost")
        scheme = "https" if (request and request.is_secure()) or not settings.DEBUG else "http"
        return f"{scheme}://{self.slug}.{root}"

    @classmethod
    def get_or_create_default(cls):
        """
        The Phase 1 data migration calls this - a 'Default Hospital' is needed
        to assign all pre-existing rows to. It is idempotent.
        """
        slug = getattr(settings, "DEFAULT_HOSPITAL_SLUG", "default")
        name = getattr(settings, "DEFAULT_HOSPITAL_NAME", "Default Hospital")
        hospital, _created = cls.objects.get_or_create(
            slug=slug, defaults={"name": name, "is_active": True}
        )
        return hospital


# No hospital can register these slugs - reserved for the platform's own pages
RESERVED_SUBDOMAINS = {
    "www", "app", "api", "admin", "mail", "smtp", "ftp", "blog", "status",
    "help", "support", "docs", "static", "media", "cdn", "assets", "test",
    "dev", "staging", "demo", "new", "beta", "account", "accounts", "billing",
    "login", "signup", "dashboard",
}


class TenantModel(models.Model):
    """
    Abstract base - every multi-tenant model inherits from it.
    The `hospital` FK is defined here so all models behave the same way.
    """

    hospital = models.ForeignKey(
        "tenants.Hospital",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
        db_index=True,
        help_text="Tenant - which hospital owns this record",
    )

    objects = TenantManager()
    all_objects = UnscopedManager()

    class Meta:
        abstract = True

    def ensure_hospital(self):
        """
        If hospital is not set, take it from the current tenant.
        Model.save() calls this FIRST - if full_clean() runs before it, the
        'hospital cannot be null' ValidationError is raised.
        """
        if self.hospital_id is None:
            hospital = context.get_current_hospital()
            if hospital is not None:
                self.hospital = hospital
            elif not context.is_tenant_enforced():
                raise ImproperlyConfigured(
                    f"{self.__class__.__name__} is being saved without a hospital and no "
                    "tenant is active. Pass `hospital=` explicitly or save inside "
                    "`with tenant_context(hospital):`."
                )
        return self.hospital

    def save(self, *args, **kwargs):
        self.ensure_hospital()
        super().save(*args, **kwargs)
