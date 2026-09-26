# Phase 1 — Multi-Tenancy

Tenant = **`Hospital`**. Har business record pe `hospital` FK hai, aur default
manager har query ko current tenant tak limit karta hai.

> **Production pe apply karne ka order (skip mat karna):**
> ```bash
> ./deploy/backup_db.sh --tag pre-phase1     # 1. backup (wajib)
> python manage.py preflight                 # 2. gate green ho
> python manage.py migrate --plan            # 3. kya chalega dekho
> python manage.py migrate                   # 4. apply
> python manage.py preflight --census        # 5. tenant breakdown + data intact
> ```

---

## 1.1 Kya bana

### `tenants` app (naya)

| File | Kaam |
|---|---|
| `tenants/models.py` | `Hospital` model + `TenantModel` abstract base (hospital FK, scoped managers, `ensure_hospital()`) |
| `tenants/managers.py` | `TenantQuerySet` / `TenantManager` (default, auto-scoped) + `UnscopedManager` (`all_objects`) |
| `tenants/context.py` | Thread-local current tenant, `tenant_context()`, `all_tenants()`, request-active flag |
| `tenants/middleware.py` | `TenantMiddleware` — subdomain → Hospital resolve, session/user fallback, cross-tenant block |
| `tenants/backends.py` | `TenantModelBackend` (tenant ke andar authenticate) + `TenantAwareModelBackend` (tenant active ho to khud ko disable) |
| `tenants/forms.py` | `TenantModelForm` / `TenantFormMixin` — FK dropdowns request-time pe re-scope |
| `tenants/admin_mixins.py` | `TenantAdminMixin` — admin tenant-scoped, FK dropdowns scoped, hospital field readonly |
| `tenants/views.py` + `urls.py` | `/healthz` (tenant-exempt) |
| `tenants/management/commands/provision_tenant.py` | Naya hospital + uska ADMIN login ek command mein |

### Tenant resolution (chosen mode: **subdomain**)

```
acme.tumhara-domain.com   ->  Hospital(slug='acme')
```

Order: **subdomain → session (`hospital_slug`) → logged-in user ka hospital**.

Settings (sab `.env` se):

| Var | Default | Matlab |
|---|---|---|
| `TENANCY_MODE` | `subdomain` | `subdomain` only (Phase 1 mein) |
| `SAAS_ROOT_DOMAIN` | `localhost` | Root domain. `ALLOWED_HOSTS` mein `.<root>` wildcard apne aap add hota hai |
| `TENANCY_SESSION_KEY` | `hospital_slug` | Session fallback key |
| `TENANT_REQUIRED` | `True` | Tenant na mile to login pe redirect |
| `TENANT_EXEMPT_PATHS` | `/accounts/login/,/admin/login/,/healthz` | Yahan enforcement skip (context phir bhi set hota hai) |
| `DEFAULT_HOSPITAL_NAME/SLUG` | `Default Hospital` / `default` | Backfill ka target |

### Data safety — 3-step migration

Har app mein:

1. `000X_*_hospital` → FK **nullable** add (existing rows block nahi hoti)
2. `tenants.0002_assign_default_hospital` → **Default Hospital** create + saara purana
   data assign (user-linked rows apne user ka hospital leti hain, child rows parent ka)
3. `000Y_*_hospital_not_null` → pehle `verify_no_null_hospital()` chalta hai; **koi row
   NULL bachi to migration RuntimeError ke saath RUK jaati hai** aur table+count batati hai

Extra: `core.IDCounter` ab per-hospital hai aur purane counters Default Hospital pe move
hote hain → `PAT-2026-0007` ke baad `0008` aayega, **numbering restart nahi hoti**.
Naya hospital apni `0001` se shuru karta hai.

### RBAC

`role_required()` / `RoleRequiredMixin` ka **permission logic bilkul nahi badla**.
Sirf ek extra check juda hai (`core.decorators.check_tenant`): user ka hospital ==
request ka hospital, warna 403. Platform super-admin (`is_platform_admin=True`,
`hospital=None`) exempt.

---

## 1.2 Jo cheezein JAAN-BOOJH ke badli (breaking, par zaroori)

| Change | Pehle | Ab | Kyun |
|---|---|---|---|
| `User.username` | globally `unique=True` | `unique=False` + `UniqueConstraint(hospital, username)` | Warna do hospitals `admin` username share nahi kar sakte the |
| `Patient.patient_id` | globally unique | `UniqueConstraint(hospital, patient_id)` | Har hospital ki apni `PAT-2026-0001` |
| `Invoice.invoice_number` | globally unique | `UniqueConstraint(hospital, invoice_number)` | Wajah wahi |
| `AUTHENTICATION_BACKENDS` | `ModelBackend` | `TenantModelBackend` + `TenantAwareModelBackend` | **Plain `ModelBackend` mat dalna** — woh global username lookup se doosre hospital ka user authenticate kar deta (test ne pakda) |
| `Payment.transaction_id` | globally unique | **waisa hi** (UUID) | Koi zaroorat nahi thi |

Naya Django check warning jo ab dikhega (expected, harmless):

```
accounts.User: (auth.W004) 'User.username' is named as the 'USERNAME_FIELD', but it is not unique.
    HINT: Ensure that your authentication backend(s) can handle non-unique usernames.
```
→ Hum handle karte hain: `TenantModelBackend` tenant ke andar lookup karta hai.

---

## 1.3 Files touched

**Naye (48 files, `git status` se verified):**

- `tenants/` app — 14 files: `__init__`, `apps`, `models`, `managers`, `context`,
  `middleware`, `backends`, `forms`, `admin`, `admin_mixins`, `views`, `urls`,
  `migrations/0001_initial`, `migrations/0002_assign_default_hospital`,
  `management/commands/provision_tenant`
- Migrations — 15 files: `accounts/0002,0003,0004`, `appointments/0004,0005`,
  `billing/0003,0004`, `core/0003`, `doctors/0002,0003`, `patients/0003,0004`,
  `pharmacy/0004,0005`, `wards/0004,0005`
- `tests/` — 6 files: `__init__`, `conftest`, `settings_test`,
  `test_tenant_isolation`, `test_core_flows`, `test_data_backfill`
- Phase 0 se: `core/management/{__init__,commands/__init__,commands/preflight}.py`
- Config/docs — `pytest.ini`, `requirements-dev.txt`, `docs/MIGRATION_PLAN.md`,
  `docs/PHASE_0_SAFETY.md`, `docs/PHASE_1_MULTITENANCY.md`

**Modified (31):** `hospital_system/settings.py` (tenants app, middleware, backends,
tenant settings, wildcard ALLOWED_HOSTS), `hospital_system/urls.py` (healthz),
`accounts/` (models/admin/forms/views), `core/` (models: IDCounter+AuditLog,
signals, decorators, middleware), `patients|doctors|appointments|billing|pharmacy|wards`
(models + forms + admin), `.env.example`, `.gitignore`, `deploy/backup_db.sh`.

**Deleted (144):** `hospital_system_delivery/` (purana duplicate copy — approved).

---

## 1.4 Verify kya hua (is sandbox mein, Django 5.2.17 pe)

```
$ python -m pytest tests/ -q
33 passed

$ python -m pytest tests/ --cov=. -q        →  TOTAL 75%
$ python manage.py migrate                  →  sab migrations OK (fresh DB)
$ python manage.py makemigrations --check   →  No changes detected
$ python manage.py preflight --census       →  ✓ secrets / DB / migrations, tenant breakdown dikhta hai
```

Test files:

| File | Tests | Kya cover |
|---|---|---|
| `tests/test_tenant_isolation.py` | 16 | **ORM scoping, HTTP 404/403, cross-tenant login block, per-hospital staff list** |
| `tests/test_core_flows.py` | 9 | RBAC (403/200), appointment booking + double-book reject, invoice→payment→**PDF**, per-hospital ID sequence |
| `tests/test_data_backfill.py` | 8 | Legacy NULL rows → Default Hospital, idempotent, **ID sequence 0007→0008 (restart nahi)**, superuser NULL rehta hai, NOT NULL safety check |

---

## 1.5 Jo verify NAHI ho saka (honest)

1. **Django 6.0.6 / Python 3.12 pe nahi chala** — sandbox mein Python 3.11.2 hai.
   Saara verification Django **5.2.17** pe hua. Tumhe Python 3.12 pe
   `pip install -r requirements.txt && pytest` chalana chahiye.
2. **PostgreSQL pe migrations test nahi hue** — yahan sirf SQLite. `ALTER TABLE
   ... SET NOT NULL` Postgres pe table lock karta hai; badi table ho to
   maintenance window mein chalao.
3. **Real wildcard DNS / Render subdomain routing test nahi hua** — uske liye
   `*.tumhara-domain.com` ka DNS + Render custom domain chahiye.

---

## 1.6 Tumhe manually kya test karna hai

```bash
# 0) Backup
./deploy/backup_db.sh --tag pre-phase1

# 1) Migrate
python manage.py migrate

# 2) Purana data Default Hospital mein gaya confirm karo
python manage.py preflight --census      # "TENANT BREAKDOWN: default  <counts>"

# 3) Doosra tenant banao (hosts file mein `127.0.0.1 acme.localhost` add karo)
python manage.py provision_tenant --name "Acme Hospital" --slug acme \
    --admin-username acme-admin --admin-email admin@acme.test --admin-password 'StrongPass!23'

# 4) Purana system abhi bhi chal raha hai (regression)
#    http://default.localhost:8000/  pe purana admin login -> data wahi dikhega
#    login (5 galat password -> axes lockout), patient register, appointment,
#    invoice + PDF download, Admin > Audit Log

# 5) Tenant isolation khud dekho
#    http://acme.localhost:8000/  pe acme-admin se login -> KHAALI dashboard
#    acme ke login se default.localhost kholo -> 403
#    default ka admin acme.localhost pe login kare -> "Invalid username or password"
```

---

## 1.7 Phase 2 se pehle jo bacha hai (jaan-na zaroori)

1. **Related managers auto-scope nahi hote.** `patient.admissions.all()`,
   `invoice.items.all()` jaise reverse relations parent se traverse hote hain —
   parent khud scoped hai isliye leak nahi, par naya code likhte waqt
   `Model.objects` hi use karo. (6 jagah identified: `billing/models.py:36,40`,
   `billing/pdf_utils.py:38,62`, `patients/views.py:53`, `pharmacy/models.py:50`.)
2. **Django admin** platform super-admin ke liye cross-tenant hai; tenant staff ke
   liye scoped. `createsuperuser` se banne wala user `hospital=None` +
   `is_platform_admin=False` hota hai — usse admin mein `is_platform_admin` tick karo.
3. **`runtime.txt` abhi bhi nahi hai** — Render pe Python 3.12 pin karna zaroori
   hai (Phase 0 wali note).
4. **Phase 2 ke liye:** `Subscription.hospital` FK isi `Hospital` model pe banega,
   aur feature-gating decorator `request.hospital` se plan padhega.
