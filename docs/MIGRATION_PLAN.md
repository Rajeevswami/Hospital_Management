# SaaS Migration — Master Plan & Status

This table is updated after every phase. Rule: **production deploys only happen
when `python manage.py preflight` is green and a DB backup has been taken.**

Base commit: `268431d360f05e1b6b63cda8506d0ae22508e847`

| Phase | Scope | Status | Docs |
|---|---|---|---|
| 0 | Branch, DB backup, `.env.example`, preflight gate | **DONE** | [docs/PHASE_0_SAFETY.md](PHASE_0_SAFETY.md) |
| 1 | Multi-tenancy: `Hospital` model, FK backfill, tenant middleware, scoped managers, tenant-aware RBAC | **DONE** (33 tests pass, 75% coverage) | [docs/PHASE_1_MULTITENANCY.md](PHASE_1_MULTITENANCY.md) |
| 2 | `Plan`, `Subscription`, Razorpay recurring, feature-gating decorator | **DONE** (45 new tests, 78 total pass, 77% coverage) | [docs/PHASE_2_SUBSCRIPTIONS.md](PHASE_2_SUBSCRIPTIONS.md) |
| 3 | `ml_engine` app: rule-based fallback → GradientBoosting, Celery task, dashboard alerts | **DONE** (35 new tests, 113 total pass, 80% coverage) | [docs/PHASE_3_ML.md](PHASE_3_ML.md) |
| 4 | DRF + tenant-aware ViewSets, JWT, drf-spectacular Swagger | **DONE** (46 new tests, 166 total pass, 84% coverage) | [docs/PHASE_4_API.md](PHASE_4_API.md) |
| 5 | pytest (≥70% coverage), tenant-isolation test, Sentry, Docker + compose, GitHub Actions | **DONE** (166 tests / 84% coverage, gate 70%; Sentry + Docker + CI added) — the Python 3.11 (Django 5.2) + 3.12 (Django 6) matrix is green on GitHub Actions, and the Docker image build + smoke test are green too | [docs/PHASE_5_OPS.md](PHASE_5_OPS.md) |
| 6 | README rewrite (Docker, .env, multi-tenant local testing) | **DONE** | [README.md](../README.md) |

## Standard sequence for running migrations (every phase)

```bash
./deploy/backup_db.sh --tag pre-phase-N        # 1. backup (mandatory)
python manage.py preflight                     # 2. gate
python manage.py makemigrations <app>          # 3. review it, do not run blindly
python manage.py migrate --plan                # 4. preview what will run
python manage.py migrate                       # 5. apply
python manage.py preflight --census            # 6. confirm the data is intact
```

## Data-loss guardrails (already decided for Phase 1)

- The `hospital` FK is first added as **nullable** → then a data migration
  assigns "Default Hospital" → then `null=False` + index. This way existing rows
  are never blocked.
- `Patient.patient_id` / `Invoice.invoice_number` are currently **globally
  unique** (`unique=True`). In a multi-tenant setup these must be **unique per
  hospital**, otherwise two hospitals cannot both create `PAT-2026-0001`. This
  change happens explicitly in Phase 1 — existing IDs stay exactly as they are.
- `core.IDCounter` counters must become per-hospital too (otherwise hospital B's
  patient IDs would start ahead of hospital A's).
