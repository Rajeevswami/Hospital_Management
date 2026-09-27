# Phase 0 — Safety Setup (SaaS Migration)

> The system is in **live production**. That is why Phase 0 does only 3 things:
> **branch**, **backup**, **env hygiene** — plus a `preflight` gate that runs
> before every phase. No business logic, model, or migration was touched in this
> phase.

---

## 0.1 Branch strategy

All of this session's work happens on the branch
**`arena/01a0dd04-hospital-management`** (the Arena session is tracked through
this branch — pushes to another branch are not associated with the session).
Content-wise this is the same branch you asked for, only the name differs.

**On your local machine**, create `feature/saas-migration` like this:

```bash
git clone https://github.com/Rajeevswami/hospital-management
cd hospital-management
git checkout -b feature/saas-migration          # branch from main (268431d)
git push -u origin feature/saas-migration
```

Or create your branch directly from this session's branch:

```bash
git fetch origin arena/01a0dd04-hospital-management
git checkout -b feature/saas-migration origin/arena/01a0dd04-hospital-management
```

Verified state (in this sandbox):

```
current branch: arena/01a0dd04-hospital-management
HEAD: 268431d360f05e1b6b63cda8506d0ae22508e847   # = tip of origin/main
worktree clean? -> 0 changed files
.env ever committed? -> NO (git log --all --diff-filter=A -- .env  = empty)
```

The rule followed going forward: **every phase = separate commit(s)**, and a
production deploy only when `python manage.py preflight` is green.

---

## 0.2 Database backup (MANDATORY before any migration)

### A) Render (live DB) — recommended

```bash
# 1. Snapshot (safest, in Render's own storage)
#    Render Dashboard > PostgreSQL instance > Manual Backup
#    or via the CLI:
render services list
render pg restore-point create <pg-service-id>

# 2. Keep a local copy too (double safety) - using the external DB URL:
export DATABASE_URL='postgres://USER:PASSWORD@HOST:5432/DB'   # Render > PostgreSQL > Info
./deploy/backup_db.sh --tag pre-saas-phase1
```

### B) VPS / self-hosted Postgres

```bash
cd /var/www/hospital-system
cp .env.example .env && nano .env          # fill in DATABASE_URL (if not set)
./deploy/backup_db.sh --tag pre-saas-phase1
```

### C) Plain one-liner (if you do not want to use the script)

```bash
pg_dump "$DATABASE_URL" -Fc -f "backups/hospital_db_pre_saas_$(date +%F_%H%M).dump"
pg_restore --list backups/hospital_db_pre_saas_*.dump | head    # integrity check
```

### Restore (rollback)

```bash
./deploy/backup_db.sh list                          # available dumps
./deploy/backup_db.sh restore backups/hospital_db_pre-saas-phase1_20260926_101500.dump
# you will have to type 'RESTORE' to confirm
```

What changed in `deploy/backup_db.sh`:

| Before | Now |
|---|---|
| DB name/user/password **hardcoded** (`hospital_db`, `hospital_user`) | read from `.env` / `DATABASE_URL` |
| Plain-text `.sql.gz` | `-Fc` custom format → integrity verification (`pg_restore --list`), selective/parallel restore |
| No way to restore | `restore` subcommand + confirmation prompt |
| All backups auto-deleted after 14 days | `--tag` backups are exempt from retention (a pre-migration backup is never deleted automatically) |
| Dumps were not verified | After a dump is written, table count + size are printed |

**Retention / cron (VPS):**

```
0 2 * * * cd /var/www/hospital_system && ./deploy/backup_db.sh >> /var/log/hospital_system/backup.log 2>&1
```

---

## 0.3 Secrets / environment variables

`.env.example` is now a **complete** template — all Phase 1–5 variables are
documented up front, so each phase also updates the env contract along with
settings.py.

```bash
cp .env.example .env          # .env is git-ignored, do not commit it
```

| Group | Variables | When required |
|---|---|---|
| Core Django | `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `RENDER_EXTERNAL_HOSTNAME`, `SECURE_PROXY_SSL_HEADER` | now |
| Database | `DATABASE_URL` | now |
| Multi-tenancy | `TENANCY_MODE`, `SAAS_ROOT_DOMAIN`, `TENANCY_SESSION_KEY`, `DEFAULT_HOSPITAL_NAME`, `DEFAULT_HOSPITAL_SLUG` | Phase 1 |
| Razorpay | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_WEBHOOK_URL`, `RAZORPAY_CURRENCY` | Phase 2 |
| Celery/Redis | `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CELERY_TASK_ALWAYS_EAGER` | Phase 3 |
| ML | `ML_MIN_TRAINING_ROWS`, `ML_MODEL_DIR`, `ML_HIGH_RISK_THRESHOLD` | Phase 3 |
| API/JWT | `SIMPLE_JWT_ACCESS_TOKEN_MINUTES`, `SIMPLE_JWT_REFRESH_TOKEN_DAYS` | Phase 4 |
| Sentry | `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_RELEASE` | Phase 5 |
| Email | `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | optional |
| Session | `SESSION_COOKIE_AGE`, `IDLE_TIMEOUT_SECONDS` | now |

On Render, put these same values into **Dashboard → Service → Environment**
(do not upload the file). Command to generate a `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

**Right now settings.py reads only 7 variables** (`SECRET_KEY`, `DEBUG`,
`ALLOWED_HOSTS`, `RENDER_EXTERNAL_HOSTNAME`, `DATABASE_URL`,
`SECURE_SSL_REDIRECT`, and `CSRF_TRUSTED_ORIGINS` derived from
`RENDER_EXTERNAL_HOSTNAME`). The remaining variables are documented in the
template; the code will read them in the phase where the feature is built —
settings.py was not touched in Phase 0, so the live deploy has zero risk.

---

## 0.4 Preflight gate

```bash
python manage.py preflight              # all checks
python manage.py preflight --census     # + row counts (Phase 3 cold-start decision)
python manage.py preflight --strict     # exit code 1 on FAIL (for CI)
python manage.py preflight --skip-secrets
```

What it checks: Python ≥ 3.12, Django ≥ 6.0, `SECRET_KEY` strength (a weak key
with DEBUG=False = FAIL), that `.env` is not tracked in git, hardcoded secrets in
tracked files (Razorpay/AWS/GitHub/Sentry/Postgres-URL patterns), DB connection,
unapplied migrations, and with `--census` the row count of every model + the ML
verdict.

Actual output in this sandbox (fresh SQLite, migrations applied):

```
  [✗] Python version: 3.11.2 - Django 6.0 requires 3.12+
  [!] Django version: 5.2.17 installed, requirements.txt demands 6.0+
  [✓] SECRET_KEY: custom key set
  [✓] .env tracking: .env is not in git (only .env.example is tracked)
  [✓] hardcoded secrets: 141 tracked files scanned, no secret found
  [✓] database: sqlite3 connected (SQLite 3.40.1)
  [✓] migrations: all migrations applied
PREFLIGHT FAILED - 1 issue(s).
```

The 2 Python/Django lines are **a limitation of this sandbox**, not an issue
with your code — see 0.6 below. The scanner's detection was verified: 4 dummy
secrets (a fake Razorpay key, Postgres URL, GitHub token, long secret) were
placed in a temp file and scanned → **4/4 detected**, then the file was removed.

---

## 0.5 Files touched in Phase 0

| File | Change |
|---|---|
| `.env.example` | Rewrite — 11 groups, Phase 1–5 variables documented |
| `deploy/backup_db.sh` | Rewrite — env-driven, `-Fc` dumps, restore/list mode, tagged backups |
| `core/management/commands/preflight.py` | **New** — safety gate command |
| `core/management/__init__.py`, `core/management/commands/__init__.py` | **New** (empty, for the package) |
| `.gitignore` | `backups/`, `*.dump`, `ml_models/`, coverage/pytest artifacts, `.env.*` |

**Migrations: none.** No model changed in Phase 0, so there is no need to run
`makemigrations` / `migrate`. A deploy is not required either — but if you do
deploy, do it after `./deploy/backup_db.sh --tag pre-saas-phase1` (adding a new
management command does not change live behaviour).

---

## 0.6 What could NOT be verified in this sandbox

An honest list — confirm these yourself on your machine/production:

1. **Python 3.12+ / Django 6.0.6 could not be installed here** (the sandbox has
   Python 3.11.2; `Django==6.0.6` demands `Requires-Python >=3.12`). So I ran
   `manage.py check`, `migrate`, and `preflight` on **Django 5.2.17**.
   The repo's code runs cleanly on 5.2 too (only 1 pre-existing warning, below).
   → **Action:** add `runtime.txt` (`python-3.12.8`) on Render, otherwise
   `pip install -r requirements.txt` may fail on the default Python version. The
   repo currently has no `runtime.txt` / `Procfile` / `nixpacks.toml` (verified).
2. **`pg_dump` / `psql` are not installed in this sandbox** (and no root for
   `apt-get`). So `backup_db.sh`'s *actual* dump path was not tested against a
   real Postgres. What was tested: `bash -n` syntax OK, missing `DATABASE_URL` →
   exit 1 with message, missing `pg_dump` → exit 1, and `DATABASE_URL` is parsed
   from `.env` and reaches the script (confirmed with stub binaries).
3. **A backup of the live Render DB cannot be taken from here** — the Render
   dashboard/CLI asks for your credentials.

Pre-existing warning (not my change; a Django 5.2/axes 8.3 check):

```
?: (axes.W006) AXES_LOCKOUT_PARAMETERS does not contain 'ip_address'.
```

This means your `AXES_LOCKOUT_PARAMETERS = ['username']` does per-username
lockout — which you did deliberately (staff share the hospital's IP). In Phase 5
we will explicitly silence or document it; brute-force protection still works.

---

## 0.7 What you need to test manually

```bash
# 1. Preflight on your machine (Python 3.12 + a real DATABASE_URL)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py preflight --census        # everything should be green (on Python 3.12)

# 2. Back up the real DB and confirm the file is created
export DATABASE_URL='postgres://...your real url...'
./deploy/backup_db.sh --tag smoke-test
./deploy/backup_db.sh list
pg_restore --list backups/hospital_db_smoke-test_*.dump | tail -3   # tables should show

# 3. Restore drill (on a STAGING DB, not production)
createdb restore_drill
DATABASE_URL='postgres://.../restore_drill' ./deploy/backup_db.sh restore backups/hospital_db_smoke-test_*.dump

# 4. The app still works normally (regression check)
python manage.py runserver
#    - login (wrong password 5 times → should lock out = axes OK)
#    - register a new patient (PAT-YYYY-000N should be generated)
#    - book an appointment, create an invoice, download the PDF
#    - entries are visible in Admin > Audit Log
```

---

## 0.8 Decisions needed before Phase 1

1. **Tenant resolution**: `subdomain` (`acme.your-domain.com`, requires DNS
   wildcard `*.domain.com`) or `path` (`domain.com/acme/`)? Render's free plan
   does not support wildcard custom domains — path mode is a safe fallback.
2. **Mapping of existing users**: will one user belong to a single hospital, or
   can one user access multiple hospitals (super-admin style)?
3. **Celery/Redis is not in the repo yet** (verified: `grep -ri celery|redis`
   → 0 results). It must be added in Phase 3 + a Redis instance on Render.
   Confirm that we should add it.
4. **The `hospital_system_delivery/` folder** is an old duplicate copy in the
   repo (its `requirements.txt` has only `django-axes`, old settings.py). Delete
   it or leave it?
