# Hospital Management SaaS

Multi-tenant hospital management system — ek hi deployment, kai hospitals. Har hospital ka
apna subdomain (`acme.example.com`), apna data (poori tarah isolated), apna plan aur billing,
aur AI-based no-show prediction.

Django 6 + PostgreSQL + Redis + Celery + scikit-learn. REST API (JWT + Swagger) ke saath.

> **Live production se SaaS bana hai** — purana single-hospital data ek "Default Hospital"
> tenant mein migrate hua hai, zero data loss. Migration ka poora record
> [`docs/MIGRATION_PLAN.md`](docs/MIGRATION_PLAN.md) mein hai.

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick start (Docker)](#quick-start-docker)
- [Manual setup (bina Docker)](#manual-setup-bina-docker)
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

| Module | Kya karta hai |
|---|---|
| Patients | Registration, per-hospital `PAT-2026-0001` IDs, history |
| Doctors | Profiles, availability, consultation fee |
| Appointments | Booking, slot-conflict check, COMPLETED / NO_SHOW / CANCELLED |
| Billing | Invoices, items, payments, PDF (ReportLab), per-hospital `INV-` numbers |
| Pharmacy | Medicines, stock, prescriptions |
| Wards | Beds, admissions, occupancy |
| Staff & RBAC | ADMIN / DOCTOR / RECEPTIONIST / PHARMACIST — role + tenant dono se gated |
| Audit log | Har critical action, user + IP ke saath |
| Security | django-axes brute-force lockout, idle session timeout, security headers |

**SaaS layer**

| Module | Kya karta hai |
|---|---|
| `tenants` | `Hospital` model, subdomain resolution, tenant-scoped managers |
| `subscriptions` | Plans, subscriptions, Razorpay recurring, feature gating |
| `ml_engine` | No-show risk score (rules → GradientBoosting), Celery async |
| `api` | DRF ViewSets, JWT auth, drf-spectacular Swagger |

---

## Architecture

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
        │  Model.objects        │  <- TenantManager: har query automatically
        │  (tenant-scoped)      │     current hospital tak limit
        └───────────┬───────────┘
                    │
              PostgreSQL
```

**Tenant isolation ke 4 layers** (defense in depth):

1. **Tenant-scoped manager** — `Patient.objects.all()` sirf current hospital ke rows deta
   hai. Tenant active na ho to silent empty result nahi, `ImproperlyConfigured` (loud fail).
2. **Per-hospital unique constraints** — `PAT-2026-0001` do hospitals mein ho sakta hai
   (`UniqueConstraint(hospital, patient_id)`), isliye IDs collide nahi karte.
3. **Cross-tenant guard** — hospital A ka user hospital B ke subdomain pe aaye to 403/404.
4. **Tenant-aware auth backend** — `TenantModelBackend` sirf current tenant ke users ko
   authenticate karta hai (dono hospitals mein same username ho sakta hai, leak nahi hota).

---

## Quick start (Docker)

```bash
git clone https://github.com/Rajeevswami/hospital-management
cd hospital-management
cp .env.example .env          # phir .env edit karo (kam se kam POSTGRES_PASSWORD)

docker compose up --build
```

Compose 4 services chalata hai: `postgres`, `redis`, `web` (gunicorn :8000), `celery`.
Entry point apne aap migrations apply karta hai aur default plans seed karta hai.

Ab pehla hospital banao:

```bash
docker compose exec web python manage.py provision_tenant \
  --name "Acme Hospital" --slug acme \
  --admin-username acmeadmin --admin-password 'ChangeMe!123'

docker compose exec web python manage.py assign_plan --hospital acme --plan scale
```

App: http://localhost:8000 (single tenant ke liye) — subdomain setup neeche dekho.
API docs: http://localhost:8000/api/docs/

---

## Manual setup (bina Docker)

Python **3.11+** chahiye. Python 3.11 pe Django **5.2 LTS** install hota hai,
Python 3.12+ pe Django 6 — dono par test suite pass karta hai
(`requirements.txt` mein pin `Django>=5.2.17,<6.1` hai).

```bash
git clone https://github.com/Rajeevswami/hospital-management.git
cd hospital-management

python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt                   # tests ke liye (optional)

cp .env.example .env          # Windows: copy .env.example .env
                              # Local ke liye isme kuch bharne ki zaroorat nahi:
                              # DATABASE_URL khaali = SQLite (db.sqlite3),
                              # DEBUG=True, SAAS_ROOT_DOMAIN=localhost,
                              # CELERY_TASK_ALWAYS_EAGER=True (Redis ki zaroorat nahi)

python manage.py migrate                              # saari migrations
python manage.py manage_plans                         # 4 plans seed (free/starter/growth/scale)

# Ek tenant (hospital) + uska admin user banao:
python manage.py provision_tenant --name "Acme Hospital" --slug acme \
  --admin-username admin --admin-password 'ChangeMe!123'
python manage.py assign_plan --hospital acme --plan scale

python manage.py runserver
```

Ab browser mein **http://localhost:8000/accounts/login/** kholo aur
`admin` / `ChangeMe!123` se login karo.

Platform admin (hospital=None, saare tenants dikhte hain) chahiye to:
`python manage.py createsuperuser`.

> `DATABASE_URL` khaali chhodne par app SQLite use karta hai. Production mein
> `DATABASE_URL=postgres://user:pass@host:5432/db` set karo.

Redis + worker (async no-show scoring ke liye):

```bash
redis-server                                # ya: docker run -p 6379:6379 redis:7-alpine
celery -A hospital_system worker -l info
```

Redis ke bina bhi chal jaayega — `.env` mein `CELERY_TASK_ALWAYS_EAGER=True` rakho,
tasks usi process mein sync chalenge (development ke liye theek, production ke liye nahi).

---

## `.env` configuration

Poora reference `.env.example` mein commented hai. Zaroori ones:

| Variable | Example | Kyun |
|---|---|---|
| `SECRET_KEY` | 50+ random chars | Django |
| `DEBUG` | `False` (prod) | |
| `DATABASE_URL` | `postgres://user:pass@host:5432/db` | |
| `SAAS_ROOT_DOMAIN` | `example.com` | Subdomain tenant resolution ka base |
| `ALLOWED_HOSTS` | `.example.com,example.com` | Wildcard ke liye **leading dot** zaroori |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `CELERY_TASK_ALWAYS_EAGER` | `True` (local) / `False` (prod) | |
| `RAZORPAY_KEY_ID` / `_KEY_SECRET` / `_WEBHOOK_SECRET` | `rzp_test_...` | Subscriptions |
| `ML_MIN_TRAINING_ROWS` | `250` | Iske pehle rule-based engine |
| `SENTRY_DSN` | `https://...@sentry.io/...` | Khaali = Sentry off |
| `POSTGRES_*`, `WEB_PORT` | | sirf docker-compose use karta hai |

`.env` kabhi commit nahi hota (`.gitignore` mein hai). `.env.example` track hota hai.

---

## Multi-tenant local testing

Do tarah se test kar sakte ho:

### A. Subdomains (production jaisa)

`/etc/hosts` mein:

```
127.0.0.1   acme.localhost beta.localhost
```

`.env`:

```ini
SAAS_ROOT_DOMAIN=localhost
ALLOWED_HOSTS=localhost,127.0.0.1,.localhost
```

Do hospitals banao:

```bash
python manage.py provision_tenant --name "Acme Hospital" --slug acme \
  --admin-username admin --admin-password 'AcmePass!123'
python manage.py provision_tenant --name "Beta Hospital" --slug beta-city \
  --admin-username admin --admin-password 'BetaPass!123'
```

> `beta` reserved subdomain hai (SaaS ke apne pages ke liye), isliye `beta-city` use kiya.

Ab `http://acme.localhost:8000` aur `http://beta.localhost:8000` — dono ka data alag,
dono ka `admin` login alag (same username, different hospital).

### B. Session fallback (bina hosts file)

Root domain (`http://localhost:8000`) pe login karo — middleware tenant ko session mein
yaad rakhta hai, isliye bina subdomain ke bhi kaam chalta hai.

### C. API ke liye header

Subdomain ke bina API call karni ho to header bhejo:

```bash
curl -H "X-Hospital-Slug: acme" http://localhost:8000/api/patients/
```

### Isolation khud verify karo

```bash
python manage.py preflight --census     # har hospital ke row counts + ML readiness
```

Ya tests: `pytest tests/test_tenant_isolation.py` (16 tests — login, ORM, forms,
middleware, API — sab levels pe isolation check).

---

## Plans & feature gating

| Plan | Price | Patients | Staff | Features |
|---|---|---|---|---|
| Free | ₹0 | 50 | 2 | core |
| Starter | ₹999 | 300 | 5 | + advanced reports |
| Growth | ₹2499 | 1500 | 20 | + SMS reminders, branding |
| Scale | ₹4999 | unlimited | 100 | + **AI no-show**, **REST API**, multi-branch |

Plans seed/update: `python manage.py manage_plans` (idempotent).

Gating kaise dikhta hai:

- **HTML** → warning message + `/saas/` (billing) pe redirect
- **API** → `402 Payment Required` + `{"reason": "not_in_plan", "feature": "api_access"}`
- **Limits** (patient/staff) → create blocked, `limit_reached`
- **Platform admin** → hamesha allowed

Naya feature add karna ho to `subscriptions/models.py` ke `Feature` enum mein entry daalo
aur plan ke `features_json` mein on karo — gating code change nahi karna padta.

Razorpay webhook: `POST /saas/webhook/` (HMAC-SHA256 verify hota hai, duplicate events
ignore hote hain). Details: [`docs/PHASE_2_SUBSCRIPTIONS.md`](docs/PHASE_2_SUBSCRIPTIONS.md).

---

## AI no-show prediction

Har appointment ka risk score (0–1) banta hai, taaki reception pehle se confirm call kare.

| Engine | Kab |
|---|---|
| **Rule-based** | Cold start — lead time, day-of-week, slot, patient history, contact quality |
| **GradientBoosting** (scikit-learn) | `ML_MIN_TRAINING_ROWS` (default 250) labelled appointments ke baad |

```bash
python manage.py no_show_census                 # kitna data hai? ML ready hai?
python manage.py score_appointments             # purane appointments backfill
python manage.py train_no_show --hospital acme  # model train + activate
```

Booking hote hi Celery task chalta hai aur `AppointmentRisk` row banata hai. High-risk
appointments dashboard pe dikhte hain, poori list `/ai/no-show/` pe (Scale plan only).

Details: [`docs/PHASE_3_ML.md`](docs/PHASE_3_ML.md).

---

## REST API

Base: `/api/` · Auth: JWT · Docs: `/api/docs/` (Swagger) aur `/api/redoc/` ·
Schema: `/api/schema/`

**Plan mein `api_access` feature zaroori hai** (Scale plan). Warna har data endpoint pe 402.

### Token lo

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

Token **tenant-scoped** hai: `acme` ka token `beta.localhost` pe kaam nahi karega (401).

### Endpoints

| Method | Path | Access |
|---|---|---|
| POST | `/api/token/`, `/api/token/refresh/`, `/api/token/verify/` | public (tenant zaroori) |
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

Sab list endpoints pe `?search=`, `?ordering=`, `?page=`, `?page_size=` (max 200) aur
declared exact-match filters (jaise `?status=PAID`, `?doctor=3`) chalte hain.

### Example

```bash
TOKEN=$(curl -s -X POST http://acme.localhost:8000/api/token/ \
  -d "username=admin&password=AcmePass!123" | python -c "import sys,json;print(json.load(sys.stdin)['access'])")

curl -H "Authorization: Bearer $TOKEN" http://acme.localhost:8000/api/patients/?search=asha
```

Details: [`docs/PHASE_4_API.md`](docs/PHASE_4_API.md).

---

## Operations

### Deploy / upgrade ka standard sequence

```bash
./deploy/backup_db.sh --tag pre-deploy        # 1. backup (wajib)
python manage.py preflight                    # 2. gate - fail ho to ruk jao
python manage.py migrate --noinput            # 3. schema
python manage.py manage_plans                 # 4. plans seed/update (idempotent)
python manage.py preflight --census           # 5. data integrity verify
```

`preflight` check karta hai: Python/Django version, SECRET_KEY, `.env`, secrets repo mein
to nahi, DB connectivity, pending migrations. `--census` har hospital ke row counts,
subscription status aur ML readiness dikhata hai.

### Backup / restore

```bash
./deploy/backup_db.sh --tag weekly            # pg_dump custom format -> deploy/backups/
./deploy/backup_db.sh list
./deploy/backup_db.sh restore deploy/backups/xxx.dump    # 'RESTORE' type karna padta hai
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

> **Note:** hosted platforms (Render/Heroku) ka filesystem ephemeral hota hai — deploy ke
> baad model files gayab ho jaati hain. Ya to release command mein retrain karo, ya
> `ml_models/` ko persistent volume / object storage banao (docker-compose mein volume hai).

### Sentry

`SENTRY_DSN` set karte hi Django + Celery integration on ho jaata hai.
`send_default_pii=False` hai — patient/staff ka PII Sentry mein nahi jaata.

---

## Tests

```bash
pytest                                        # 159 tests
pytest --cov=. --cov-fail-under=70            # coverage gate (abhi ~83%)
pytest tests/test_tenant_isolation.py         # sirf isolation
pytest tests/test_api.py                      # sirf API
```

| Suite | Tests | Kya cover karta hai |
|---|---|---|
| `test_tenant_isolation.py` | 16 | login, ORM, forms, middleware, cross-tenant 404 |
| `test_core_flows.py` | 9 | booking, billing + PDF, RBAC |
| `test_data_backfill.py` | 8 | purana data → Default Hospital (migration rewind) |
| `test_subscriptions.py` | 45 | plans, Razorpay webhook, gating |
| `test_no_show.py` | 35 | rules, features/leakage, Celery, training, commands |
| `test_api.py` | 46 | JWT, RBAC, tenant isolation, gating, Swagger |

CI (`.github/workflows/ci.yml`) har push pe: Django checks → pending-migration check →
pytest with coverage gate → OpenAPI schema validation → Docker image build + smoke test.

---

## Deployment

### Docker Compose (VPS / single box)

```bash
docker compose up -d --build
docker compose logs -f web celery
```

Reverse proxy (nginx/caddy) se `*.example.com` ko `web:8000` pe bhejo, aur DNS wildcard
(`*.example.com`) set karo.

### Render / Heroku

- Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
- Start: `gunicorn hospital_system.wsgi:application --bind 0.0.0.0:$PORT`
- Release: `python manage.py migrate --noinput && python manage.py manage_plans`
- Ek **Redis** instance chahiye (Celery broker) aur ek **Celery worker** service
- `SAAS_ROOT_DOMAIN=example.com`, `ALLOWED_HOSTS=.example.com,example.com`
- ML models: ephemeral filesystem — release command mein `train_no_show` chalao

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

| Problem | Wajah / fix |
|---|---|
| `DisallowedHost` (400) | `ALLOWED_HOSTS` mein `.example.com` (leading dot) daalo |
| Pehli request pe 500, traceback ajeeb | URLconf lazy import + tenant-scoped manager. `wsgi.py`/`asgi.py` pehle hi URLconf preload karte hain — custom entrypoint use kar rahe ho to wahi karo |
| `ImproperlyConfigured: No tenant is active` | Request ke bahar `Model.objects` use ho raha hai. `with tenant_context(hospital):` use karo |
| `/api/...` pe 401 "Tenant resolve nahi hua" | Subdomain use karo ya `X-Hospital-Slug` header |
| API pe 402 `not_in_plan` | Plan mein `api_access` nahi — `assign_plan --plan scale` |
| Risk score nahi ban raha | Celery worker nahi chal raha, ya `CELERY_TASK_ALWAYS_EAGER=True` nahi hai. `manage.py score_appointments` se backfill karo |
| `train_no_show` kehta hai "COLD START" | Normal — `ML_MIN_TRAINING_ROWS` se kam data hai. Rule-based engine chalta rahega |
| Username globally unique error | `accounts` migrations apply nahi hue — `manage.py migrate` |
| Razorpay webhook 400 | `RAZORPAY_WEBHOOK_SECRET` set nahi, ya raw body ke bajaye parsed JSON pe signature verify ho raha hai |

---

## License

Private / proprietary — abhi koi open-source license nahi hai.
