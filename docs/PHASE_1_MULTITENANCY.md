# Phase 1 — Multi-Tenancy

Tenant = **`Hospital`**. Every business record has a `hospital` FK, and the
default manager limits every query to the current tenant.

> **Order to apply on production (do not skip):**
> ```bash
> ./deploy/backup_db.sh --tag pre-phase1     # 1. backup (mandatory)
> python manage.py preflight                 # 2. gate must be green
> python manage.py migrate --plan            # 3. preview what will run
> python manage.py migrate                   # 4. apply
> python manage.py preflight --census        # 5. tenant breakdown + data intact
> ```

---

## 1.1 What was built

### `tenants` app (new)

| File | Purpose |
|---|---|
| `tenants/models.py` | `Hospital` model + `TenantModel` abstract base (hospital FK, scoped managers, `ensure_hospital()`) |
| `tenants/managers.py` | `TenantQuerySet` / `TenantManager` (default, auto-scoped) + `UnscopedManager` (`all_objects`) |
| `tenants/context.py` | Thread-local current tenant, `tenant_context()`, `all_tenants()`, request-active flag |
| `tenants/middleware.py` | `TenantMiddleware` — subdomain → Hospital resolution, session/user fallback, cross-tenant block |
| `tenants/backends.py` | `TenantModelBackend` (authenticates within a tenant) + `TenantAwareModelBackend` (disables itself when a tenant is active) |
| `tenants/forms.py` | `TenantModelForm` / `TenantFormMixin` — FK dropdowns re-scope at request time |
| `tenants/admin_mixins.py` | `TenantAdminMixin` — admin is tenant-scoped, FK dropdowns scoped, hospital field readonly |
| `tenants/views.py` + `urls.py` | `/healthz` (tenant-exempt) |
| `tenants/management/commands/provision_tenant.py` | A new hospital + its ADMIN login in a single command |

### Tenant resolution (chosen mode: **subdomain**)

```
acme.your-domain.com   ->  Hospital(slug='acme')
```

Order: **subdomain → session (`hospital_slug`) → the logged-in user's hospital**.

Settings (all from `.env`):

| Var | Default | Meaning |
|---|---|---|
| `TENANCY_MODE` | `subdomain` | `subdomain` only (in Phase 1) |
| `SAAS_ROOT_DOMAIN` | `localhost` | Root domain. The `.<root>` wildcard is added to `ALLOWED_HOSTS` automatically |
| `TENANCY_SESSION_KEY` | `hospital_slug` | Session fallback key |
| `TENANT_REQUIRED` | `True` | Redirect to login when no tenant is found |
| `TENANT_EXEMPT_PATHS` | `/accounts/login/,/admin/login/,/healthz` | Enforcement is skipped here (context is still set) |
| `DEFAULT_HOSPITAL_NAME/SLUG` | `Default Hospital` / `default` | Target of the backfill |

### Data safety — 3-step migration

In every app:

1. `000X_*_hospital` → add the FK as **nullable** (existing rows are not blocked)
2. `tenants.0002_assign_default_hospital` → create **Default Hospital** + assign
   all pre-existing data (user-linked rows take their user's hospital, child rows
   take their parent's)
3. `000Y_*_hospital_not_null` → first `verify_no_null_hospital()` runs; **if any
   row is still NULL the migration STOPS with a RuntimeError** and reports the
   table + count

Extra: `core.IDCounter` is now per-hospital and old counters are moved to
Default Hospital → after `PAT-2026-0007` comes `0008`, **numbering does not
restart**. A new hospital starts at its own `0001`.

### RBAC

The **permission logic of `role_required()` / `RoleRequiredMixin` did not change
at all**. Only one extra check was added (`core.decorators.check_tenant`): the
user's hospital must equal the request's hospital, otherwise 403. Platform
super-admins (`is_platform_admin=True`, `hospital=None`) are exempt.

---

## 1.2 Changes made DELIBERATELY (breaking, but necessary)

| Change | Before | Now | Why |
|---|---|---|---|
| `User.username` | globally `unique=True` | `unique=False` + `UniqueConstraint(hospital, username)` | Otherwise two hospitals could not both use the `admin` username |
| `Patient.patient_id` | globally unique | `UniqueConstraint(hospital, patient_id)` | Every hospital gets its own `PAT-2026-0001` |
| `Invoice.invoice_number` | globally unique | `UniqueConstraint(hospital, invoice_number)` | Same reason |
| `AUTHENTICATION_BACKENDS` | `ModelBackend` | `TenantModelBackend` + `TenantAwareModelBackend` | **Do not add plain `ModelBackend`** — it authenticates a user from another hospital via global username lookup (a test caught this) |
| `Payment.transaction_id` | globally unique | **unchanged** (UUID) | There was no need |

A new Django check warning that now appears (expected, harmless):

```
accounts.User: (auth.W004) 'User.username' is named as the 'USERNAME_FIELD', but it is not unique.
    HINT: Ensure that your authentication backend(s) can handle non-unique usernames.
```
→ We handle this: `TenantModelBackend` performs the lookup within the tenant.

---

## 1.3 Files touched

**New (48 files, verified with `git status`):**

- `tenants/` app — 14 files: `__init__`, `apps`, `models`, `managers`, `context`,
  `middleware`, `backends`, `forms`, `admin`, `admin_mixins`, `views`, `urls`,
  `migrations/0001_initial`, `migrations/0002_assign_default_hospital`,
  `management/commands/provision_tenant`
- Migrations — 15 files: `accounts/0002,0003,0004`, `appointments/0004,0005`,
  `billing/0003,0004`, `core/0003`, `doctors/0002,0003`, `patients/0003,0004`,
  `pharmacy/0004,0005`, `wards/0004,0005`
- `tests/` — 6 files: `__init__`, `conftest`, `settings_test`,
  `test_tenant_isolation`, `test_core_flows`, `test_data_backfill`
- From Phase 0: `core/management/{__init__,commands/__init__,commands/preflight}.py`
- Config/docs — `pytest.ini`, `requirements-dev.txt`, `docs/MIGRATION_PLAN.md`,
  `docs/PHASE_0_SAFETY.md`, `docs/PHASE_1_MULTITENANCY.md`

**Modified (31):** `hospital_system/settings.py` (tenants app, middleware, backends,
tenant settings, wildcard ALLOWED_HOSTS), `hospital_system/urls.py` (healthz),
`accounts/` (models/admin/forms/views), `core/` (models: IDCounter+AuditLog,
signals, decorators, middleware), `patients|doctors|appointments|billing|pharmacy|wards`
(models + forms + admin), `.env.example`, `.gitignore`, `deploy/backup_db.sh`.

**Deleted (144):** `hospital_system_delivery/` (old duplicate copy — approved).

---

## 1.4 What was verified (in this sandbox, on Django 5.2.17)

```
$ python -m pytest tests/ -q
33 passed

$ python -m pytest tests/ --cov=. -q        →  TOTAL 75%
$ python manage.py migrate                  →  all migrations OK (fresh DB)
$ python manage.py makemigrations --check   →  No changes detected
$ python manage.py preflight --census       →  ✓ secrets / DB / migrations, tenant breakdown visible
```

Test files:

| File | Tests | Coverage |
|---|---|---|
| `tests/test_tenant_isolation.py` | 16 | **ORM scoping, HTTP 404/403, cross-tenant login block, per-hospital staff list** |
| `tests/test_core_flows.py` | 9 | RBAC (403/200), appointment booking + double-book reject, invoice→payment→**PDF**, per-hospital ID sequence |
| `tests/test_data_backfill.py` | 8 | Legacy NULL rows → Default Hospital, idempotent, **ID sequence 0007→0008 (no restart)**, superuser stays NULL, NOT NULL safety check |

---

## 1.5 What could NOT be verified (honest)

1. **Not run on Django 6.0.6 / Python 3.12** — the sandbox has Python 3.11.2.
   All verification was done on Django **5.2.17**. You should run
   `pip install -r requirements.txt && pytest` on Python 3.12.
2. **Migrations not tested on PostgreSQL** — only SQLite here. `ALTER TABLE
   ... SET NOT NULL` takes a table lock on Postgres; run it in a maintenance
   window if the table is large.
3. **Real wildcard DNS / Render subdomain routing not tested** — that requires
   DNS for `*.your-domain.com` + a Render custom domain.

---

## 1.6 What you need to test manually

```bash
# 0) Backup
./deploy/backup_db.sh --tag pre-phase1

# 1) Migrate
python manage.py migrate

# 2) Confirm the old data landed in Default Hospital
python manage.py preflight --census      # "TENANT BREAKDOWN: default  <counts>"

# 3) Create a second tenant (add `127.0.0.1 acme.localhost` to your hosts file)
python manage.py provision_tenant --name "Acme Hospital" --slug acme \
    --admin-username acme-admin --admin-email admin@acme.test --admin-password 'StrongPass!23'

# 4) The old system still works (regression)
#    log in with the old admin at http://default.localhost:8000/ -> same data
#    login (5 wrong passwords -> axes lockout), patient register, appointment,
#    invoice + PDF download, Admin > Audit Log

# 5) See tenant isolation for yourself
#    log in as acme-admin at http://acme.localhost:8000/ -> EMPTY dashboard
#    open default.localhost with an acme login -> 403
#    log in as the default admin on acme.localhost -> "Invalid username or password"
```

---

## 1.7 What remains before Phase 2 (must-know)

1. **Related managers are not auto-scoped.** Reverse relations like
   `patient.admissions.all()`, `invoice.items.all()` traverse from the parent —
   the parent is itself scoped, so there is no leak, but new code should use
   `Model.objects`. (6 places identified: `billing/models.py:36,40`,
   `billing/pdf_utils.py:38,62`, `patients/views.py:53`, `pharmacy/models.py:50`.)
2. **Django admin** is cross-tenant for platform super-admins; scoped for tenant
   staff. A user created with `createsuperuser` gets `hospital=None` +
   `is_platform_admin=False` — tick `is_platform_admin` for them in admin.
3. **`runtime.txt` is still missing** — pinning Python 3.12 on Render is
   required (see the Phase 0 note).
4. **For Phase 2:** `Subscription.hospital` FK will be built on this `Hospital`
   model, and the feature-gating decorator will read the plan from
   `request.hospital`.
