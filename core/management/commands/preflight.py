"""
Phase 0 safety gate for the SaaS migration.

Why: the system is in live production. Before starting every phase, confirm with
a single command that (a) the code runs, (b) secrets come only from the env,
(c) migrations are applied, (d) how much data exists (this exact number is
needed for the Phase 3 cold-start decision).

USAGE
    python manage.py preflight                 # all checks
    python manage.py preflight --census        # + row counts (Phase 3 planning)
    python manage.py preflight --strict        # exit code 1 on any FAIL (for CI)
    python manage.py preflight --skip-secrets  # skip the secret scan
"""
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

MIN_PYTHON = (3, 12)     # Django 6.0 runs on this (requirements.txt Django==6.0.6)
MIN_DJANGO = (6, 0)

# Folders excluded from the secret scan (vendor / duplicate copies)
SECRET_SCAN_SKIP = ("hospital_system_delivery", "node_modules", ".venv", "venv")

# Such dummy passwords appear in docs/examples - do not treat them as secrets
PLACEHOLDER_PASSWORDS = {
    "password", "pass", "secret", "changeme", "change-me", "your_password",
    "yourpassword", "strong_password", "put_strong_password_here",
    "change-me-strong-password", "xxx",
    "test", "example", "dummy", "postgres", "redacted", "changemeinproduction",
}

SECRET_PATTERNS = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub / GitHub Actions token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Razorpay key id", re.compile(r"\brzp_(?:live|test)_[A-Za-z0-9]{8,}\b")),
    ("OpenAI / Anthropic style key", re.compile(r"\b(?:sk|sk-ant)-[A-Za-z0-9_\-]{20,}\b")),
    ("Sentry DSN", re.compile(r"\bhttps://[0-9a-f]{32}@[a-z0-9.\-]+/\d+\b", re.I)),
    ("Postgres URL with inline password",
     re.compile(r"\bpostgres(?:ql)?://[^\s:@/]+:(?!\$|<)[^\s@]+@[^\s/]+", re.I)),
    ("Long base64/hex secret assigned to a variable",
     re.compile(r"(?i)\b(?:secret|token|passwd|password|api_?key)\s*=\s*[\"'][A-Za-z0-9+/=_\-]{32,}[\"']")),
]

# Models for the census - this shows how much data the ML has in Phase 3
CENSUS_MODELS = [
    ("tenants", "Hospital"),
    ("subscriptions", "Plan"),
    ("subscriptions", "Subscription"),
    ("accounts", "User"),
    ("patients", "Patient"),
    ("doctors", "Doctor"),
    ("appointments", "Appointment"),
    ("pharmacy", "Prescription"),
    ("pharmacy", "Medicine"),
    ("billing", "Invoice"),
    ("billing", "Payment"),
    ("wards", "Admission"),
    ("core", "AuditLog"),
]


class Command(BaseCommand):
    help = "Safety check before the SaaS migration: env config, secrets, DB, migrations, data census."

    def add_arguments(self, parser):
        parser.add_argument("--census", action="store_true", help="Also compute row counts (read-only)")
        parser.add_argument("--strict", action="store_true", help="Exit code 1 on any FAIL")
        parser.add_argument("--skip-secrets", action="store_true", help="Skip the secret scan")

    def handle(self, *args, **opts):
        self.results = []
        self.check_python()
        self.check_django()
        self.check_secret_key()
        self.check_env_file_not_tracked()
        if not opts["skip_secrets"]:
            self.check_hardcoded_secrets()
        self.check_database()
        self.check_unapplied_migrations()
        if opts["census"]:
            self.print_census()
        self.print_summary(strict=opts["strict"])

    # ---------------- helpers ----------------
    def record(self, status, name, detail):
        self.results.append((status, name, detail))
        icon = {"ok": "\u2713", "warn": "!", "fail": "\u2717"}[status]
        self.stdout.write(f"  [{icon}] {name}: {detail}")

    def git(self, *args):
        """List of tracked files; None if git is unavailable."""
        try:
            out = subprocess.run(
                ["git", *args], capture_output=True, text=True, check=True,
                cwd=settings.BASE_DIR,
            ).stdout
            return out
        except Exception:
            return None

    # ---------------- checks ----------------
    def check_python(self):
        v = sys.version_info
        if v[:2] >= MIN_PYTHON:
            self.record("ok", "Python version", f"{v.major}.{v.minor}.{v.micro} (>= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})")
        else:
            self.record("fail", "Python version",
                        f"{v.major}.{v.minor}.{v.micro} - Django 6.0 requires {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")

    def check_django(self):
        import django
        parts = tuple(int(x) for x in django.VERSION[:2])
        if parts >= MIN_DJANGO:
            self.record("ok", "Django version", f"{django.get_version()} (>= {MIN_DJANGO[0]}.{MIN_DJANGO[1]})")
        else:
            self.record("warn", "Django version",
                        f"{django.get_version()} installed, requirements.txt demands {MIN_DJANGO[0]}.{MIN_DJANGO[1]}+")

    def check_secret_key(self):
        key = settings.SECRET_KEY or ""
        insecure = key.startswith("django-insecure-") or "CHANGE-THIS" in key
        if settings.DEBUG:
            status = "warn" if insecure else "ok"
            self.record(status, "SECRET_KEY",
                        "dev placeholder (DEBUG=True, fine for local use)" if insecure else "custom key set")
        elif insecure or len(key) < 50:
            self.record("fail", "SECRET_KEY", "weak/default key with DEBUG=False - generate a new one for production")
        else:
            self.record("ok", "SECRET_KEY", f"set from env (len={len(key)})")

    def check_env_file_not_tracked(self):
        tracked = self.git("ls-files")
        if tracked is None:
            self.record("warn", ".env tracking", "git not available - check manually that .env is not committed")
            return
        files = tracked.splitlines()
        bad = [f for f in files if f == ".env" or f.endswith("/.env")]
        if bad:
            self.record("fail", ".env tracking", f"COMMITTED: {', '.join(bad)} -> git rm --cached is required")
        else:
            self.record("ok", ".env tracking", ".env is not in git (only .env.example is tracked)")

    @staticmethod
    def _is_placeholder_url(url):
        """postgres://user:STRONG_PASSWORD@host -> this is an example, not a real secret."""
        try:
            password = url.split(":", 2)[2].rsplit("@", 1)[0].lower()
        except IndexError:
            return False
        return password in PLACEHOLDER_PASSWORDS or password.startswith(("your", "<", "$"))

    def check_hardcoded_secrets(self):
        tracked = self.git("ls-files")
        if tracked is None:
            self.record("warn", "hardcoded secrets", "git not available - scan skipped")
            return
        hits = []
        scanned = 0
        for rel in tracked.splitlines():
            if any(part in rel.split("/") for part in SECRET_SCAN_SKIP):
                continue
            path = Path(settings.BASE_DIR) / rel
            if not path.is_file() or path.suffix not in (".py", ".txt", ".md", ".sh", ".yml", ".yaml", ".cfg", ".toml", ".json", ".html", ""):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                # do not flag .env.example placeholders / obvious templates
                if stripped.startswith("#") or "change-me" in line or "CHANGE-THIS" in line or "<" in line:
                    continue
                for label, pattern in SECRET_PATTERNS:
                    match = pattern.search(line)
                    if not match:
                        continue
                    if label.startswith("Postgres URL") and self._is_placeholder_url(match.group(0)):
                        continue
                    hits.append(f"{rel}:{lineno} [{label}]")
                    break
        if hits:
            self.record("fail", "hardcoded secrets", f"{len(hits)} hit(s): " + "; ".join(hits[:6]))
        else:
            self.record("ok", "hardcoded secrets", f"{scanned} tracked files scanned, no secret found")

    def check_database(self):
        try:
            connection.ensure_connection()
            with connection.cursor() as cur:
                # a different query per vendor - version() does not exist on SQLite
                query = {"postgresql": "SELECT version()",
                         "sqlite": "SELECT 'SQLite ' || sqlite_version()",
                         "mysql": "SELECT VERSION()"}.get(connection.vendor, "SELECT version()")
                cur.execute(query)
                version = cur.fetchone()[0].split(",")[0]
            engine = settings.DATABASES["default"]["ENGINE"].split(".")[-1]
            self.record("ok", "database", f"{engine} connected ({version})")
        except Exception as exc:
            self.record("fail", "database", f"could not connect: {exc}")

    def check_unapplied_migrations(self):
        try:
            from django.core.management import call_command
            from io import StringIO
            out = StringIO()
            call_command("showmigrations", "--list", stdout=out, verbosity=0)
            text = out.getvalue()
        except Exception as exc:
            self.record("warn", "migrations", f"showmigrations could not run: {exc}")
            return
        unapplied = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("[ ]")]
        if unapplied:
            self.record("warn", "migrations", f"{len(unapplied)} unapplied: " + ", ".join(unapplied[:5]))
        else:
            self.record("ok", "migrations", "all migrations applied")

    # ---------------- census ----------------
    def print_census(self):
        from django.apps import apps
        self.stdout.write("")
        # Tenant-wise breakdown: how much data each hospital has
        try:
            from tenants.models import Hospital
            from tenants.context import all_tenants
            with all_tenants():
                rows = [
                    (h.slug,
                     apps.get_model("patients", "Patient").all_objects.filter(hospital=h).count(),
                     apps.get_model("appointments", "Appointment").all_objects.filter(hospital=h).count(),
                     apps.get_model("accounts", "User").all_objects.filter(hospital=h).count())
                    for h in Hospital.objects.all()
                ]
            if rows:
                self.stdout.write("  TENANT BREAKDOWN (slug: patients / appointments / staff):")
                for slug, pat, appt, staff in rows:
                    self.stdout.write(f"      {slug:<20} {pat:>6} / {appt:>6} / {staff:>4}")
                orphans = apps.get_model("patients", "Patient").all_objects.filter(hospital__isnull=True).count()
                if orphans:
                    self.stdout.write(self.style.ERROR(f"      {orphans} patient(s) without a hospital (orphans!)"))
        except Exception as exc:
            self.stdout.write(f"      tenant breakdown failed: {exc}")

        # Subscription coverage - how many hospitals have a usable plan
        try:
            from subscriptions.models import Subscription
            from tenants.models import Hospital
            total = Hospital.objects.count()
            with_subs = Subscription.all_objects.filter(hospital__isnull=False).count()
            accessible = sum(
                1 for s in Subscription.all_objects.select_related("plan") if s.is_accessible
            )
            missing = max(total - accessible, 0)
            self.stdout.write(
                f"      subscriptions: {accessible}/{total} hospitals accessible "
                f"({missing} without a usable subscription)"
            )
            if accessible == 0 and total > 0:
                self.stdout.write(self.style.WARNING(
                    "      No hospital is accessible -> new feature blocks are active. "
                    "Run `manage.py manage_plans` + `assign_plan`."
                ))
        except Exception as exc:
            self.stdout.write(f"      subscription check failed: {exc}")
        self.stdout.write("  DATA CENSUS (read-only counts) - for the Phase 3 cold-start decision:")
        for app_label, model_name in CENSUS_MODELS:
            try:
                model = apps.get_model(app_label, model_name)
                count = model.objects.count()
            except Exception as exc:
                self.stdout.write(f"      {app_label}.{model_name}: n/a ({exc.__class__.__name__})")
                continue
            self.stdout.write(f"      {app_label}.{model_name:<14} {count}")
        # The most important number for ML: labelled appointments (past, resolved status)
        try:
            from appointments.models import Appointment
            resolved = Appointment.objects.filter(
                status__in=["COMPLETED", "NO_SHOW", "CANCELLED"]
            ).count()
            self.stdout.write(f"      {'ML labelled rows':<34} {resolved}  (COMPLETED + NO_SHOW + CANCELLED)")
            self.stdout.write(f"      {'ML_MIN_TRAINING_ROWS':<34} {getattr(settings, 'ML_MIN_TRAINING_ROWS', 250)}")
            verdict = "ML model can be trained" if resolved >= getattr(settings, "ML_MIN_TRAINING_ROWS", 250) \
                else "COLD START -> rule-based fallback will be used (Phase 3.2)"
            self.stdout.write(f"      verdict: {verdict}")
        except Exception as exc:
            self.stdout.write(f"      appointments count failed: {exc}")

    # ---------------- summary ----------------
    def print_summary(self, strict):
        fails = [r for r in self.results if r[0] == "fail"]
        warns = [r for r in self.results if r[0] == "warn"]
        self.stdout.write("")
        if fails:
            self.stdout.write(self.style.ERROR(f"PREFLIGHT FAILED - {len(fails)} issue(s). Fix them before migrating."))
        elif warns:
            self.stdout.write(self.style.WARNING(f"PREFLIGHT OK with {len(warns)} warning(s)."))
        else:
            self.stdout.write(self.style.SUCCESS("PREFLIGHT PASSED - you can proceed with the phase."))
        if strict and fails:
            raise SystemExit(1)
