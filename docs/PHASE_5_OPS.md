# Phase 5 — Tests, Sentry, Docker, CI/CD

Scope of this phase: pytest suite + coverage gate, one specific cross-tenant
isolation test, Sentry, Dockerfile + docker-compose (web/postgres/redis/celery),
and GitHub Actions CI/CD.

---

## 1. Test suite

| Suite | Tests | What it covers |
|---|---|---|
| `tests/test_tenant_isolation.py` | 16 | **cross-tenant isolation** (details below) |
| `tests/test_core_flows.py` | 12 | booking, billing + PDF, RBAC, staff/doctor create form |
| `tests/test_data_backfill.py` | 8 | old data → "Default Hospital" (with migration rewind) |
| `tests/test_subscriptions.py` | 45 | plans, Razorpay webhook, feature gating |
| `tests/test_no_show.py` | 35 | rules, features/leakage, Celery, training, commands |
| `tests/test_api.py` | 46 | JWT, RBAC, tenant isolation, 402 gating, Swagger |
| `tests/test_settings_blank_env.py` | 4 | blank `.env` values (DATABASE_URL / ALLOWED_HOSTS) do not crash |
| **Total** | **166** | **coverage 84%** (gate 70%) |

How to run:

```bash
pytest                                              # everything
pytest --cov=. --cov-fail-under=70                  # the CI gate
pytest tests/test_tenant_isolation.py -v            # isolation only
```

Config: `pytest.ini` (repo root) + `tests/settings_test.py` (WhiteNoise manifest
storage off, Celery eager, temp `ML_MODEL_DIR`, small `ML_MIN_TRAINING_ROWS`).

### Cross-tenant isolation test (specific requirement)

`tests/test_tenant_isolation.py` — two hospitals (`acme`, `beta-city`), checked
at every level:

| Level | What is asserted |
|---|---|
| **ORM** | `Patient.objects.all()` in A's context does not return B's rows; querying without a tenant raises `ImproperlyConfigured` (not a silent empty result) |
| **Login** | B's user cannot log in on A's subdomain (even with the same username) |
| **Detail view** | B's patient pk on A's view → 404 (even the existence is not revealed) |
| **Forms** | A's form dropdowns do not list B's patients/doctors |
| **Middleware** | A's user on B's subdomain → 403 |
| **ID sequences** | each hospital runs its own `PAT-2026-0001` sequence; they do not collide |
| **API** | A's JWT on B's subdomain → 401; tenant switch via `X-Hospital-Slug` → blocked |

And `tests/test_data_backfill.py` **rewinds** the migration to verify that old
rows (hospital=NULL) were assigned to "Default Hospital" and no row was deleted.

---

## 2. Sentry

```ini
SENTRY_DSN=https://<key>@o0.ingest.sentry.io/<project>
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.1
SENTRY_RELEASE=<git-sha>
```

In `settings.py`, init happens **only** when `SENTRY_DSN` is set — local dev is
completely unaffected.

```python
sentry_sdk.init(
    dsn=SENTRY_DSN,
    environment=SENTRY_ENVIRONMENT,
    traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
    integrations=[DjangoIntegration(), CeleryIntegration()],
    send_default_pii=False,        # this is hospital data - do not send PII
    release=...,
)
```

`send_default_pii=False` is deliberate: patient/staff emails and IPs never go to
Sentry. The error context includes the request path, the user **id** and the
`hospital` slug (DjangoIntegration default behaviour), no PII.

Celery integration is on, so failures of the no-show scoring task are tracked
too.

---

## 3. Docker

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.12-slim`, deps, `collectstatic`, non-root `appuser`, healthcheck |
| `docker-compose.yml` | `postgres` + `redis` + `web` (gunicorn) + `celery` |
| `deploy/docker-entrypoint.sh` | DB wait → `migrate` → `manage_plans` → CMD |
| `.dockerignore` | `.git`, `.env`, `db.sqlite3`, `backups/`, `ml_models/` stay out of the image |

```bash
cp .env.example .env        # change POSTGRES_PASSWORD!
docker compose up --build
```

Design decisions:

- **Healthcheck-gated startup** — `web` and `celery` start only after
  `postgres`/`redis` are *healthy*; the entrypoint still waits for the DB itself
  (it does not rely on compose).
- **Migrations run only in `web`** — `SKIP_MIGRATIONS=1` in the `celery`
  container, otherwise the two containers race.
- **Volumes** — `postgres_data`, `redis_data`, `static_files`, `media_files`, and
  `ml_models` (so trained joblib models survive a deploy).
- **Non-root user** (`appuser`) + writable dirs.
- A throwaway `SECRET_KEY` for build-time `collectstatic` — no real secret goes
  into the image; everything comes from `.env` at runtime.

Inside compose, service names double as hostnames, so the compose file builds
`DATABASE_URL` and `REDIS_URL` itself (no need to fill them in separately in
`.env`).

---

## 4. CI/CD — `.github/workflows/ci.yml`

**Job 1: `test`** (with postgres 16 + redis 7 service containers)

1. Python 3.12 setup + pip cache
2. System deps (`libpq-dev`, `libjpeg-dev`, `zlib1g-dev`)
3. `pip install -r requirements.txt -r requirements-dev.txt`
4. `python manage.py check`
5. `python manage.py makemigrations --check --dry-run` — **fails on any pending migration**
6. `pytest --cov=. --cov-fail-under=70` (coverage gate)
7. `python manage.py spectacular --file ... --validate` (OpenAPI schema)
8. `coverage.xml` artifact upload

**Job 2: `docker`** (`needs: test`)

1. `docker build -t hospital-saas:ci .`
2. `python manage.py check` inside the image (smoke test)

Trigger: push to `main` + `arena/**`, all PRs, and manual `workflow_dispatch`.
No secrets required — dummy env for the tests is inline.

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

No new migrations. The standard sequence is unchanged:

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
  -> 166 passed
  -> Required test coverage of 70% reached. Total coverage: 82.89%

python manage.py spectacular --file /tmp/schema.yml --validate
  -> Errors: 0 (0 unique), Warnings: 18 (7 unique), 56 KB schema

python manage.py makemigrations --check --dry-run   -> No changes detected
python manage.py check                              -> 2 issues (both pre-existing)
bash -n deploy/docker-entrypoint.sh                 -> OK
docker-compose.yml + ci.yml                         -> YAML parse OK (PyYAML)
```

---

## 8. What was NOT verified (honestly)

| Item | Status |
|---|---|
| `docker build` / `docker compose up` | **Unverified** — this sandbox has no Docker daemon. The Dockerfile/compose files were written, YAML + shell syntax checked, but no image was built. Run `docker compose up --build` on your machine to confirm. |
| An actual GitHub Actions run | **Unverified** — the workflow only runs once pushed. YAML parsing has been verified. |
| Sentry receiving real events | **Unverified** — without a DSN, init does not happen at all. Fill in the DSN and deliberately trigger a 500 to verify. |
| Test suite on Postgres | **Unverified locally** — local runs used SQLite. CI runs on Postgres 16; check the first CI run. |

---

## 9. Manual test steps

```bash
# Docker
cp .env.example .env            # change POSTGRES_PASSWORD
docker compose up --build
docker compose ps               # postgres/redis healthy, web/celery running
curl http://localhost:8000/healthz
docker compose exec web python manage.py preflight --census

# Celery connectivity
docker compose exec web celery -A hospital_system call hospital_system.debug_ping
docker compose logs -f celery   # book an appointment and look for "Appointment N scored"

# CI
git push                        # GitHub Actions -> watch the run in the Actions tab
```

---

## 10. Notes for Phase 6

The README must document all of: Docker quick start, `.env` reference,
multi-tenant local testing (subdomain + `/etc/hosts` + `X-Hospital-Slug`), API
usage, and troubleshooting.
