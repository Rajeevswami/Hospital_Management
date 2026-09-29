# Hospital Management SaaS

Multi-tenant hospital management system — one deployment, many hospitals. Each
hospital gets its own subdomain (`acme.example.com`), its own data (fully
isolated), its own plan and billing, plus AI-based no-show prediction.

Django 6 + PostgreSQL + Redis + Celery + scikit-learn. With a REST API
(JWT + Swagger).

![Hospital Management SaaS](docs/images/hero.png)

> **Built from a live production system into a SaaS** — the old single-hospital
> data was migrated into a "Default Hospital" tenant with zero data loss. The
> full migration record is in
> [`docs/MIGRATION_PLAN.md`](docs/MIGRATION_PLAN.md).

---

## Contents

- [Features](#features)
- [Product screenshots](#product-screenshots)
- [Architecture](#architecture)
- [Quick start (Docker)](#quick-start-docker)
- [Manual setup (without Docker)](#manual-setup-without-docker)
- [`.env` configuration](#env-configuration)
- [Multi-tenant local testing](#multi-tenant-local-testing)
- [Plans & feature gating](#plans--feature-gating)
- [AI no-show prediction](#ai-no-show-prediction)
- [REST API](#rest-api)
- [Operations](#operations)
- [Tests](#tests)
- [Deployment](#deployment)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)

---

## Features

**Clinical / admin**

| Module | What it does |
|---|---|
| Patients | Registration, per-hospital `PAT-2026-0001` IDs, history |
| Doctors | Profiles, availability, consultation fee |
| Appointments | Booking, slot-conflict check, COMPLETED / NO_SHOW / CANCELLED |
| Billing | Invoices, items, payments, PDF (ReportLab), per-hospital `INV-` numbers |
| Pharmacy | Medicines, stock, prescriptions |
| Wards | Beds, admissions, occupancy |
| Staff & RBAC | ADMIN / DOCTOR / RECEPTIONIST / PHARMACIST — gated by both role and tenant |
| Audit log | Every critical action, with user + IP |
| Security | django-axes brute-force lockout, idle session timeout, security headers |

**SaaS layer**

| Module | What it does |
|---|---|
| `tenants` | `Hospital` model, subdomain resolution, tenant-scoped managers |
| `subscriptions` | Plans, subscriptions, Razorpay recurring, feature gating |
| `ml_engine` | No-show risk score (rules → GradientBoosting), Celery async |
| `api` | DRF ViewSets, JWT auth, drf-spectacular Swagger |

---

## Product screenshots

> Screenshots of the real product UI, rendered with representative sample data
> (Indian names, `PAT-2026-XXXX` patient IDs, ₹ amounts). Layouts and styling
> match the Django/Bootstrap templates in `templates/`.

**Dashboard overview** — admin home with KPI stat cards, 7-day revenue line
chart, bed-occupancy doughnut, today's appointments table, and a High Risk
Appointments (AI No-Show) panel:

![Dashboard overview](docs/images/dashboard-overview.png)

**Patients list** — searchable patient directory with `PAT-2026-XXXX` IDs,
demographics and blood group:

![Patients list](docs/images/patients-list.png)

**AI no-show risk** — every upcoming appointment scored 0–1 by the
`ml_engine` (rules → GradientBoosting), with HIGH/MEDIUM/LOW risk badges and
human-readable reasons for reception to act on:

![AI no-show risk dashboard](docs/images/dashboard-ai-risk.png)

**Service modules** — the clinical/admin modules included in every deployment
(patients, doctors, appointments, billing, pharmacy, wards, RBAC, audit log):

![Service modules](docs/images/services-modules.png)

**Plans & pricing** — the SaaS subscription plans with per-plan limits and
feature gating (Free → Starter → Growth → Scale):

![Plans and pricing](docs/images/services-plans.png)

---

## Architecture

![Architecture](docs/images/architecture.svg)

```
                 acme.example.com        beta.example.com
                          \                    /
                           \                  /
                    ┌──────────────────────────┐
                    │  TenantMiddleware        │  Host / X-Hospital-Slug
                    │  -> request.hospital     │  -> tenant context (thread-local)
                    └───────────┬──────────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        │                       │                        │
   HTML views              REST API (/api)          Celery worker
   (session auth)          (JWT + api_access)       (tenant_context manually)
        │                       │                        │
        └───────────┬───────────┴────────────────────────┘
                    │
        ┌───────────▼───────────┐
        │  Model.objects        │  <- TenantManager: every query is automatically
        │  (tenant-scoped)      │     limited to the current hospital
        └───────────┬───────────┘
                    │
              PostgreSQL
```

**4 layers of tenant isolation** (defense in depth):

1. **Tenant-scoped manager** — `Patient.objects.all()` returns only the current
   hospital's rows. If no tenant is active you do not get a silent empty result,
   you get `ImproperlyConfigured` (loud fail).
2. **Per-hospital unique constraints** — `PAT-2026-0001` can exist in two
   hospitals (`UniqueConstraint(hospital, patient_id)`), so IDs never collide.
3. **Cross-tenant guard** — a hospital A user hitting hospital B's subdomain
   gets 403/404.
4. **Tenant-aware auth backend** — `TenantModelBackend` authenticates only the
   current tenant's users (both hospitals can have the same username; nothing
   leaks).

---

## Quick start (Docker)

```bash
git clone https://github.com/Rajeevswami/hospital-management
cd hospital-management
cp .env.example .env          # then edit .env (at least POSTGRES_PASSWORD)

docker compose up --build
```

Compose runs 4 services: `postgres`, `redis`, `web` (gunicorn :8000), `celery`.
The entry point applies migrations and seeds the default plans automatically.

Now create the first hospital:

```bash
docker compose exec web python manage.py provision_tenant \
  --name "Acme Hospital" --slug acme \
  --admin-username acmeadmin --admin-password 'ChangeMe!123'

docker compose exec web python manage.py assign_plan --hospital acme --plan scale
```

App: http://localhost:8000 (for a single tenant) — see the subdomain setup below.
API docs: http://localhost:8000/api/docs/

---

## Manual setup (without Docker)

Python **3.11+** is required. Python 3.11 installs Django **5.2 LTS**, Python
3.12+ installs Django 6 — the test suite passes on both (the pin in
`requirements.txt` is `Django>=5.2.17,<6.1`).

```bash
git clone https://github.com/Rajeevswami/hospital-management.git
cd hospital-management

python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt                   # for tests (optional)

cp .env.example .env          # Windows: copy .env.example .env
                              # For local use you do not need to fill anything in:
                              # empty DATABASE_URL = SQLite (db.sqlite3),
                              # DEBUG=True, SAAS_ROOT_DOMAIN=localhost,
                              # CELERY_TASK_ALWAYS_EAGER=True (no Redis needed)

python manage.py migrate                              # all migrations
python manage.py manage_plans                         # seed 4 plans (free/starter/growth/scale)

# Create one tenant (hospital) + its admin user:
python manage.py provision_tenant --name "Acme Hospital" --slug acme \
  --admin-username admin --admin-password 'ChangeMe!123'
python manage.py assign_plan --hospital acme --plan scale

python manage.py runserver
```

Now open **http://localhost:8000/accounts/login/** in a browser and log in with
`admin` / `ChangeMe!123`.

If you need a platform admin (hospital=None, sees all tenants):
`python manage.py createsuperuser`.

> With an empty `DATABASE_URL` the app uses SQLite. In production set
> `DATABASE_URL=postgres://user:pass@host:5432/db`.

Redis + worker (for async no-show scoring):

```bash
redis-server                                # or: docker run -p 6379:6379 redis:7-alpine
celery -A hospital_system worker -l info
```

It also works without Redis — keep `CELERY_TASK_ALWAYS_EAGER=True` in `.env`,
and tasks run synchronously in the same process (fine for development, not for
production).

---

## `.env` configuration

The full reference is commented in `.env.example`. The essential ones:

| Variable | Example | Why |
|---|---|---|
| `SECRET_KEY` | 50+ random chars | Django |
| `DEBUG` | `False` (prod) | |
| `DATABASE_URL` | `postgres://user:pass@host:5432/db` | |
| `SAAS_ROOT_DOMAIN` | `example.com` | Base for subdomain tenant resolution |
| `ALLOWED_HOSTS` | `.example.com,example.com` | A **leading dot** is required for the wildcard |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `CELERY_TASK_ALWAYS_EAGER` | `True` (local) / `False` (prod) | |
| `RAZORPAY_KEY_ID` / `_KEY_SECRET` / `_WEBHOOK_SECRET` | `rzp_test_...` | Subscriptions |
| `ML_MIN_TRAINING_ROWS` | `250` | Rule-based engine until then |
| `SENTRY_DSN` | `https://...@sentry.io/...` | Empty = Sentry off |
| `POSTGRES_*`, `WEB_PORT` | | used only by docker-compose |

`.env` is never committed (it is in `.gitignore`). `.env.example` is tracked.

---

## Multi-tenant local testing

You can test in two ways:

### A. Subdomains (like production)

In `/etc/hosts`:

```
127.0.0.1   acme.localhost beta.localhost
```

`.env`:

```ini
SAAS_ROOT_DOMAIN=localhost
ALLOWED_HOSTS=localhost,127.0.0.1,.localhost
```

Create two hospitals:

```bash
python manage.py provision_tenant --name "Acme Hospital" --slug acme \
  --admin-username admin --admin-password 'AcmePass!123'
python manage.py provision_tenant --name "Beta Hospital" --slug beta-city \
  --admin-username admin --admin-password 'BetaPass!123'
```

> `beta` is a reserved subdomain (for the SaaS's own pages), which is why
> `beta-city` is used here.

Now `http://acme.localhost:8000` and `http://beta.localhost:8000` — separate data
for each, separate `admin` logins (same username, different hospital).

### B. Session fallback (without a hosts file)

Log in on the root domain (`http://localhost:8000`) — the middleware keeps the
tenant in the session, so it works without a subdomain too.

### C. Header for the API

To make API calls without a subdomain, send the header:

```bash
curl -H "X-Hospital-Slug: acme" http://localhost:8000/api/patients/
```

### Verify isolation yourself

```bash
python manage.py preflight --census     # per-hospital row counts + ML readiness
```

Or the tests: `pytest tests/test_tenant_isolation.py` (16 tests — login, ORM,
forms, middleware, API — isolation checked at every level).

---

## Plans & feature gating

| Plan | Price | Patients | Staff | Features |
|---|---|---|---|---|
| Free | ₹0 | 50 | 2 | core |
| Starter | ₹999 | 300 | 5 | + advanced reports |
| Growth | ₹2499 | 1500 | 20 | + SMS reminders, branding |
| Scale | ₹4999 | unlimited | 100 | + **AI no-show**, **REST API**, multi-branch |

Seed/update plans: `python manage.py manage_plans` (idempotent).

What gating looks like:

- **HTML** → warning message + redirect to `/saas/` (billing)
- **API** → `402 Payment Required` + `{"reason": "not_in_plan", "feature": "api_access"}`
- **Limits** (patient/staff) → create blocked, `limit_reached`
- **Platform admin** → always allowed

To add a new feature, add an entry to the `Feature` enum in
`subscriptions/models.py` and turn it on in the plan's `features_json` — the
gating code does not change.

Razorpay webhook: `POST /saas/webhook/` (HMAC-SHA256 signature verified,
duplicate events ignored). Details:
[`docs/PHASE_2_SUBSCRIPTIONS.md`](docs/PHASE_2_SUBSCRIPTIONS.md).

---

## AI no-show prediction

Every appointment gets a risk score (0–1), so reception can confirm in advance
with a call.

| Engine | When |
|---|---|
| **Rule-based** | Cold start — lead time, day-of-week, slot, patient history, contact quality |
| **GradientBoosting** (scikit-learn) | after `ML_MIN_TRAINING_ROWS` (default 250) labelled appointments |

```bash
python manage.py no_show_census                 # how much data? is ML ready?
python manage.py score_appointments             # backfill old appointments
python manage.py train_no_show --hospital acme  # train + activate the model
```

On booking, a Celery task runs and creates an `AppointmentRisk` row. High-risk
appointments appear on the dashboard, the full list at `/ai/no-show/` (Scale plan
only).

Details: [`docs/PHASE_3_ML.md`](docs/PHASE_3_ML.md).

---

## REST API

Base: `/api/` · Auth: JWT · Docs: `/api/docs/` (Swagger) and `/api/redoc/` ·
Schema: `/api/schema/`

**The plan must include the `api_access` feature** (Scale plan). Otherwise every
data endpoint returns 402.

### Get a token

```bash
curl -X POST http://acme.localhost:8000/api/token/ \
  -d "username=admin&password=AcmePass!123"
```

```json
{
  "access": "eyJ...", "refresh": "eyJ...",
  "user": {"id": 3, "username": "admin", "role": "ADMIN",
           "hospital": "acme", "hospital_name": "Acme Hospital"}
}
```

The token is **tenant-scoped**: an `acme` token will not work on
`beta.localhost` (401).

### Endpoints

| Method | Path | Access |
|---|---|---|
| POST | `/api/token/`, `/api/token/refresh/`, `/api/token/verify/` | public (tenant required) |
| GET | `/api/` | public — endpoint discovery |
| GET | `/api/me/`, `/api/hospital/`, `/api/subscription/` | any staff |
| GET | `/api/features/?feature=api_access` | any staff |
| CRUD | `/api/patients/` | read: all · write: ADMIN, RECEPTIONIST |
| CRUD | `/api/doctors/` | read: all · write: ADMIN |
| CRUD | `/api/appointments/` | read: all · write: ADMIN, RECEPTIONIST, DOCTOR |
| GET | `/api/appointments/today/` | any staff |
| PATCH | `/api/appointments/<id>/status/` | ADMIN, RECEPTIONIST, DOCTOR |
| GET | `/api/appointments/<id>/risk/` | any staff |
| GET | `/api/patients/<id>/appointments/` | any staff |
| GET | `/api/invoices/` (read-only) | ADMIN, RECEPTIONIST |
| GET | `/api/staff/` | ADMIN |
| GET | `/api/risks/`, `/api/risks/high/` | any staff |

On all list endpoints, `?search=`, `?ordering=`, `?page=`, `?page_size=` (max
200) and declared exact-match filters (like `?status=PAID`, `?doctor=3`) work.

### Example

```bash
TOKEN=$(curl -s -X POST http://acme.localhost:8000/api/token/ \
  -d "username=admin&password=AcmePass!123" | python -c "import sys,json;print(json.load(sys.stdin)['access'])")

curl -H "Authorization: Bearer $TOKEN" http://acme.localhost:8000/api/patients/?search=asha
```

Details: [`docs/PHASE_4_API.md`](docs/PHASE_4_API.md).

---

## Operations

### Standard deploy / upgrade sequence

```bash
./deploy/backup_db.sh --tag pre-deploy        # 1. backup (mandatory)
python manage.py preflight                    # 2. gate - stop if it fails
python manage.py migrate --noinput            # 3. schema
python manage.py manage_plans                 # 4. seed/update plans (idempotent)
python manage.py preflight --census           # 5. verify data integrity
```

`preflight` checks: Python/Django version, SECRET_KEY, `.env`, secrets in the
repo, DB connectivity, pending migrations. `--census` shows per-hospital row
counts, subscription status and ML readiness.

### Backup / restore

```bash
./deploy/backup_db.sh --tag weekly            # pg_dump custom format -> deploy/backups/
./deploy/backup_db.sh list
./deploy/backup_db.sh restore deploy/backups/xxx.dump    # you must type 'RESTORE'
```

### Celery worker

```bash
celery -A hospital_system worker -l info
celery -A hospital_system call hospital_system.debug_ping   # connectivity check
```

### ML model retrain (cron / release command)

```bash
python manage.py train_no_show --hospital acme
```

> **Note:** hosted platforms (Render/Heroku) have an ephemeral filesystem —
> model files disappear after a deploy. Either retrain in the release command,
> or make `ml_models/` a persistent volume / object storage (docker-compose
> already has a volume).

### Sentry

Setting `SENTRY_DSN` enables the Django + Celery integration.
`send_default_pii=False` — patient/staff PII never goes to Sentry.

---

## Tests

```bash
pytest                                        # 166 tests
pytest --cov=. --cov-fail-under=70            # coverage gate (currently ~83%)
pytest tests/test_tenant_isolation.py         # isolation only
pytest tests/test_api.py                      # API only
```

| Suite | Tests | What it covers |
|---|---|---|
| `test_tenant_isolation.py` | 16 | login, ORM, forms, middleware, cross-tenant 404 |
| `test_core_flows.py` | 12 | booking, billing + PDF, RBAC, staff/doctor create form |
| `test_data_backfill.py` | 8 | old data → Default Hospital (migration rewind) |
| `test_subscriptions.py` | 45 | plans, Razorpay webhook, gating |
| `test_no_show.py` | 35 | rules, features/leakage, Celery, training, commands |
| `test_api.py` | 46 | JWT, RBAC, tenant isolation, gating, Swagger |
| `test_settings_blank_env.py` | 4 | blank `.env` values (DATABASE_URL / ALLOWED_HOSTS) must not break settings |

CI (`.github/workflows/ci.yml`) on every push: Django checks → pending-migration
check → pytest with coverage gate → OpenAPI schema validation → Docker image
build + smoke test.

pytest runs in a **Python 3.11 (Django 5.2 LTS) and 3.12 (Django 6)** matrix
against Postgres 16 + Redis service containers. Latest run: 166 passed / 84%
coverage on both Python versions.

---

## Deployment

### Docker Compose (VPS / single box)

```bash
docker compose up -d --build
docker compose logs -f web celery
```

Point `*.example.com` at `web:8000` from a reverse proxy (nginx/caddy), and set
the DNS wildcard (`*.example.com`).

### Render / Heroku

- Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
- Start: `gunicorn hospital_system.wsgi:application --bind 0.0.0.0:$PORT`
- Release: `python manage.py migrate --noinput && python manage.py manage_plans`
- You need a **Redis** instance (Celery broker) and a **Celery worker** service
- `SAAS_ROOT_DOMAIN=example.com`, `ALLOWED_HOSTS=.example.com,example.com`
- ML models: ephemeral filesystem — run `train_no_show` in the release command

Details: [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## Project structure

```
hospital_system/     settings, urls, wsgi/asgi, celery
tenants/             Hospital model, middleware, scoped managers, auth backend
subscriptions/       Plan, Subscription, Razorpay, feature gating
ml_engine/           no-show prediction (rules + sklearn), Celery task
api/                 DRF viewsets, serializers, permissions, JWT, Swagger
accounts/            User (per-hospital username), RBAC, staff
patients/ doctors/ appointments/ billing/ pharmacy/ wards/ dashboard/ core/
templates/           Bootstrap 5 templates
deploy/              backup script, docker entrypoint
docs/                phase-wise documentation
tests/               pytest suite
```

Docs index: [`docs/MIGRATION_PLAN.md`](docs/MIGRATION_PLAN.md) ·
[Phase 0 safety](docs/PHASE_0_SAFETY.md) ·
[Phase 1 multi-tenancy](docs/PHASE_1_MULTITENANCY.md) ·
[Phase 2 subscriptions](docs/PHASE_2_SUBSCRIPTIONS.md) ·
[Phase 3 ML](docs/PHASE_3_ML.md) ·
[Phase 4 API](docs/PHASE_4_API.md) ·
[Phase 5 ops](docs/PHASE_5_OPS.md)

---

## Troubleshooting

| Problem | Cause / fix |
|---|---|
| `DisallowedHost` (400) | Add `.example.com` (leading dot) to `ALLOWED_HOSTS` |
| 500 on the first request, weird traceback | URLconf lazy import + tenant-scoped manager. `wsgi.py`/`asgi.py` already preload the URLconf — do the same if you use a custom entrypoint |
| `ImproperlyConfigured: No tenant is active` | `Model.objects` is being used outside a request. Use `with tenant_context(hospital):` |
| 401 "Tenant could not be resolved" on `/api/...` | Use a subdomain or the `X-Hospital-Slug` header |
| 402 `not_in_plan` on the API | The plan has no `api_access` — `assign_plan --plan scale` |
| Risk score not being created | The Celery worker is not running, or `CELERY_TASK_ALWAYS_EAGER=True` is not set. Backfill with `manage.py score_appointments` |
| `train_no_show` says "COLD START" | Normal — less data than `ML_MIN_TRAINING_ROWS`. The rule-based engine keeps working |
| Username globally unique error | The `accounts` migrations are not applied — `manage.py migrate` |
| Razorpay webhook 400 | `RAZORPAY_WEBHOOK_SECRET` is not set, or the signature is verified against parsed JSON instead of the raw body |

---

## License

Private / proprietary — there is no open-source license yet.
