"""
Creates a new tenant (hospital) + its first ADMIN login.

For local multi-tenant testing and new customer onboarding:

    python manage.py provision_tenant --name "Acme Hospital" --slug acme \\
        --admin-username acme-admin --admin-email admin@acme.test --admin-password 'StrongPass!23'

In subdomain mode:  http://acme.localhost:8000/  (you must add
`127.0.0.1 acme.localhost` to your hosts file).
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from tenants.context import tenant_context
from tenants.models import Hospital


class Command(BaseCommand):
    help = "Create a new hospital (tenant) + its ADMIN user."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Hospital name")
        parser.add_argument("--slug", help="Subdomain (default: slugified from the name)")
        parser.add_argument("--admin-username", required=True)
        parser.add_argument("--admin-email", default="")
        parser.add_argument("--admin-password", required=True)
        parser.add_argument("--inactive", action="store_true", help="Create the hospital as suspended")

    @transaction.atomic
    def handle(self, *args, **opts):
        slug = opts["slug"]
        hospital, created = Hospital.objects.get_or_create(
            slug=slug,
            defaults={
                "name": opts["name"],
                "contact_email": opts["admin_email"],
                "is_active": not opts["inactive"],
            },
        )
        if not created:
            raise CommandError(f"Hospital with slug '{slug}' already exists (id={hospital.pk}).")

        with tenant_context(hospital):
            if User.all_objects.filter(hospital=hospital, username=opts["admin_username"]).exists():
                raise CommandError("That admin username already exists for this hospital.")
            admin_user = User(
                username=opts["admin_username"],
                email=opts["admin_email"],
                hospital=hospital,
                role=User.Role.ADMIN,
                is_staff=True,
            )
            admin_user.set_password(opts["admin_password"])
            admin_user.save()

        self.stdout.write(self.style.SUCCESS(
            f"Tenant '{hospital.name}' created (slug={hospital.slug}, id={hospital.pk})\n"
            f"  admin login : {opts['admin_username']}\n"
            f"  subdomain   : {hospital.slug}.<SAAS_ROOT_DOMAIN>"
        ))
