"""
Tenant model - ek Hospital = ek customer = ek isolated data set.

Subdomain scheme (Phase 1 mein chosen):
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
    NOTE: Hospital khud tenant nahi hai (iska apna manager tenant-scoped nahi),
    warna "kaunsa hospital exist karta hai" resolve hi nahi ho paata.
    """

    name = models.CharField(max_length=150, help_text="Hospital ka display naam")
    slug = models.SlugField(
        max_length=63, unique=True, db_index=True,
        help_text="Subdomain - acme.example.com ke liye 'acme'. Lowercase, no spaces.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="False = subscription lapse / suspend. Login block ho jaata hai, data safe rehta hai.",
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
            raise ValidationError("Slug ya name zaroori hai.")
        if slug in RESERVED_SUBDOMAINS:
            raise ValidationError(
                f"'{slug}' reserved hai (platform ke apne pages ke liye). Doosra slug chuno."
            )

    def save(self, *args, **kwargs):
        # slug normalize + reserved check har save pe (shell se banaya ho ya form se)
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
        Phase 1 data migration isi ko call karti hai - purane saare rows ko
        assign karne ke liye ek 'Default Hospital' chahiye. Idempotent hai.
        """
        slug = getattr(settings, "DEFAULT_HOSPITAL_SLUG", "default")
        name = getattr(settings, "DEFAULT_HOSPITAL_NAME", "Default Hospital")
        hospital, _created = cls.objects.get_or_create(
            slug=slug, defaults={"name": name, "is_active": True}
        )
        return hospital


# In slugs pe koi hospital register nahi ho sakta - platform ke apne pages ke liye reserved
RESERVED_SUBDOMAINS = {
    "www", "app", "api", "admin", "mail", "smtp", "ftp", "blog", "status",
    "help", "support", "docs", "static", "media", "cdn", "assets", "test",
    "dev", "staging", "demo", "new", "beta", "account", "accounts", "billing",
    "login", "signup", "dashboard",
}


class TenantModel(models.Model):
    """
    Abstract base - har multi-tenant model ise inherit karta hai.
    `hospital` FK yahan define hai taaki sab models ka behaviour ek jaisa rahe.
    """

    hospital = models.ForeignKey(
        "tenants.Hospital",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
        db_index=True,
        help_text="Tenant - kaunsa hospital yeh record own karta hai",
    )

    objects = TenantManager()
    all_objects = UnscopedManager()

    class Meta:
        abstract = True

    def ensure_hospital(self):
        """
        hospital set nahi hai to current tenant se le lo.
        Model.save() isse SABSE PEHLE call karta hai - isse pehle full_clean()
        chalaya to 'hospital cannot be null' ValidationError aa jaata hai.
        """
        if self.hospital_id is None:
            hospital = context.get_current_hospital()
            if hospital is not None:
                self.hospital = hospital
            elif not context.is_tenant_enforced():
                raise ImproperlyConfigured(
                    f"{self.__class__.__name__} save ho raha hai bina hospital ke aur koi "
                    "tenant active nahi hai. `hospital=` explicitly do ya "
                    "`with tenant_context(hospital):` ke andar save karo."
                )
        return self.hospital

    def save(self, *args, **kwargs):
        self.ensure_hospital()
        super().save(*args, **kwargs)
