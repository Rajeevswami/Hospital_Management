# Phase 5 — Tests, Sentry, Docker, CI/CD

Is phase ka scope: pytest suite + coverage gate, ek specific cross-tenant isolation test,
Sentry, Dockerfile + docker-compose (web/postgres/redis/celery), aur GitHub Actions CI/CD.

---

## 1. Test suite

| Suite | Tests | Kya cover karta hai |
|---|---|---|
| `tests/test_tenant_isolation.py` | 16 | **cross-tenant isolation** (neeche detail) |
| `tests/test_core_flows.py` | 9 | booking, billing + PDF, RBAC |
| `tests/test_data_backfill.py` | 8 | purana data → "Default Hospital" (migration rewind ke saath) |
| `tests/test_subscriptions.py` | 45 | plans, Razorpay webhook, feature gating |
| `tests/test_no_show.py` | 35 | rules, features/leakage, Celery, training, commands |
| `tests/test_api.py` | 46 | JWT, RBAC, tenant isolation, 402 gating, Swagger |
| **Total** | **159** | **coverage 82.89%** (gate 70%) |

Chalane ka tareeka:

```bash
pytest                                              # sab
pytest --cov=. --cov-fail-under=70                  # CI wala gate
pytest tests/test_tenant_isolation.py -v            # sirf isolation
```

Config: `pytest.ini` (repo root) + `tests/settings_test.py` (WhiteNoise manifest storage
off, Celery eager, temp `ML_MODEL_DIR`, chhota `ML_MIN_TRAINING_ROWS`).

### Cross-tenant isolation test (specific requirement)

`tests/test_tenant_isolation.py` — do hospitals (`acme`, `beta-city`), har level pe check:

| Level | Kya assert hota hai |
|---|---|
| **ORM** | `Patient.objects.all()` A ke context mein B ka row nahi deta; bina tenant ke query `ImproperlyConfigured` deta hai (silent empty nahi) |
| **Login** | B ka user A ke subdomain pe login nahi kar sakta (same username hone par bhi) |
| **Detail view** | B ka patient pk A ke view pe → 404 (existence bhi pata na chale) |
| **Forms** | A ke form ke dropdown mein B ke patients/doctors nahi aate |
| **Middleware** | A ka user B ke subdomain pe → 403 |
| **ID sequences** | dono hospitals ka `PAT-2026-0001` alag-alag chalega, collide nahi |
| **API** | A ka JWT B ke subdomain pe → 401; `X-Hospital-Slug` se tenant switch → blocked |

Aur `tests/test_data_backfill.py` migration ko **rewind** karke check karta hai ki purane
rows (hospital=NULL) "Default Hospital" ko assign hue, koi row delete nahi hua.

---

## 2. Sentry

```ini
SENTRY_DSN=https://<key>@o0.ingest.sentry.io/<project>
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.1
SENTRY_RELEASE=<git-sha>
```

`settings.py` mein init **sirf tab** hota hai jab `SENTRY_DSN` bhara ho — local dev bilkul
unaffected.

```python
sentry_sdk.init(
    dsn=SENTRY_DSN,
    environment=SENTRY_ENVIRONMENT,
    traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
    integrations=[DjangoIntegration(), CeleryIntegration()],
    send_default_pii=False,        # hospital data hai - PII mat bhejo
    release=...,
)
```

`send_default_pii=False` jaan-boojh ke: patient/staff ke email aur IP Sentry mein nahi
jaate. Error context mein request path, user **id** aur `hospital` slug milta hai
(DjangoIntegration default behaviour), PII nahi.

Celery integration on hai, isliye no-show scoring task ke failures bhi track hote hain.

---

## 3. Docker

| File | Kaam |
|---|---|
| `Dockerfile` | `python:3.12-slim`, deps, `collectstatic`, non-root `appuser`, healthcheck |
| `docker-compose.yml` | `postgres` + `redis` + `web` (gunicorn) + `celery` |
| `deploy/docker-entrypoint.sh` | DB wait → `migrate` → `manage_plans` → CMD |
| `.dockerignore` | `.git`, `.env`, `db.sqlite3`, `backups/`, `ml_models/` image mein nahi jaate |

```bash
cp .env.example .env        # POSTGRES_PASSWORD zaroor badlo
docker compose up --build
```

Design ke decisions:

- **Healthcheck-gated startup** — `web` aur `celery` `postgres`/`redis` ke *healthy* hone
  ke baad hi start hote hain; entrypoint phir bhi khud DB wait karta hai (compose ke
  bharose nahi).
- **Migrations sirf `web` chalata hai** — `celery` container mein `SKIP_MIGRATIONS=1`,
  warna do containers race karte.
- **Volumes** — `postgres_data`, `redis_data`, `static_files`, `media_files`, aur
  `ml_models` (trained joblib models deploy pe udd na jaayein).
- **Non-root user** (`appuser`) + writable dirs.
- Build-time `collectstatic` ke liye throwaway `SECRET_KEY` — image mein koi real secret
  nahi jaata, sab runtime pe `.env` se.

Compose ke andar service names hi hostnames hain, isliye `DATABASE_URL` aur `REDIS_URL`
compose file khud banati hai (`.env` mein alag se bharne ki zaroorat nahi).

---

## 4. CI/CD — `.github/workflows/ci.yml`

**Job 1: `test`** (postgres 16 + redis 7 service containers ke saath)

1. Python 3.12 setup + pip cache
2. System deps (`libpq-dev`, `libjpeg-dev`, `zlib1g-dev`)
3. `pip install -r requirements.txt -r requirements-dev.txt`
4. `python manage.py check`
5. `python manage.py makemigrations --check --dry-run` — **pending migration ho to fail**
6. `pytest --cov=. --cov-fail-under=70` (coverage gate)
7. `python manage.py spectacular --file ... --validate` (OpenAPI schema)
8. `coverage.xml` artifact upload

**Job 2: `docker`** (`needs: test`)

1. `docker build -t hospital-saas:ci .`
2. Image ke andar `python manage.py check` (smoke test)

Trigger: `main` + `arena/**` pe push, sab PRs, aur manual `workflow_dispatch`.
Koi secret zaroori nahi — tests ke liye dummy env inline hai.

---

## 5. Files touched

**New:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`,
`deploy/docker-entrypoint.sh`, `.github/workflows/ci.yml`, `requirements-dev.txt`,
`docs/PHASE_5_OPS.md`

**Modified:** `hospital_system/settings.py` (Sentry init + `SENTRY_*`),
`requirements.txt` (`sentry-sdk`), `.env.example` (Sentry + Docker section),
`docs/MIGRATION_PLAN.md`

---

## 6. Migration commands

Koi naya migration nahi. Standard sequence wahi:

```bash
./deploy/backup_db.sh --tag pre-phase5
python manage.py preflight
python manage.py migrate --noinput
python manage.py preflight --census
```

---

## 7. Verification

```
pytest tests/ --cov=. --cov-fail-under=70
  -> 159 passed
  -> Required test coverage of 70% reached. Total coverage: 82.89%

python manage.py spectacular --file /tmp/schema.yml --validate
  -> Errors: 0 (0 unique), Warnings: 18 (7 unique), 56 KB schema

python manage.py makemigrations --check --dry-run   -> No changes detected
python manage.py check                              -> 2 issues (dono pre-existing)
bash -n deploy/docker-entrypoint.sh                 -> OK
docker-compose.yml + ci.yml                         -> YAML parse OK (PyYAML)
```

---

## 8. Jo verify NAHI hua (honestly)

| Cheez | Status |
|---|---|
| `docker build` / `docker compose up` | **Unverified** — is sandbox mein Docker daemon nahi hai. Dockerfile/compose likhe gaye hain, YAML + shell syntax check hua, image build nahi hua. Apni machine pe `docker compose up --build` chala kar confirm karo. |
| GitHub Actions ka actual run | **Unverified** — workflow push hone par hi chalega. YAML parse verify hua hai. |
| Sentry ko real events milna | **Unverified** — DSN ke bina init hi nahi hota. DSN bhar ke jaan-boojh kar ek 500 kara ke verify karo. |
| Postgres pe test suite | **Unverified locally** — local runs SQLite pe hue. CI Postgres 16 pe chalata hai; pehla CI run dekh lo. |

---

## 9. Manual test steps

```bash
# Docker
cp .env.example .env            # POSTGRES_PASSWORD badlo
docker compose up --build
docker compose ps               # postgres/redis healthy, web/celery running
curl http://localhost:8000/healthz
docker compose exec web python manage.py preflight --census

# Celery connectivity
docker compose exec web celery -A hospital_system call hospital_system.debug_ping
docker compose logs -f celery   # booking karke "Appointment N scored" dekho

# CI
git push                        # GitHub Actions -> Actions tab mein run dekho
```

---

## 10. Phase 6 ke liye notes

README mein yeh sab document karna hai: Docker quick start, `.env` reference,
multi-tenant local testing (subdomain + `/etc/hosts` + `X-Hospital-Slug`), API usage,
aur troubleshooting.
