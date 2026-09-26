# SaaS Migration — Master Plan & Status

Har phase ke baad yeh table update hoga. Rule: **production deploy sirf tab jab
`python manage.py preflight` green ho aur DB backup liya ja chuka ho.**

Base commit: `268431d360f05e1b6b63cda8506d0ae22508e847`

| Phase | Scope | Status | Docs |
|---|---|---|---|
| 0 | Branch, DB backup, `.env.example`, preflight gate | **DONE** | [docs/PHASE_0_SAFETY.md](PHASE_0_SAFETY.md) |
| 1 | Multi-tenancy: `Hospital` model, FK backfill, tenant middleware, scoped managers, tenant-aware RBAC | **DONE** (33 tests pass, 75% coverage) | [docs/PHASE_1_MULTITENANCY.md](PHASE_1_MULTITENANCY.md) |
| 2 | `Plan`, `Subscription`, Razorpay recurring, feature-gating decorator | **DONE** (45 naye tests, 78 total pass, 77% coverage) | [docs/PHASE_2_SUBSCRIPTIONS.md](PHASE_2_SUBSCRIPTIONS.md) |
| 3 | `ml_engine` app: rule-based fallback → GradientBoosting, Celery task, dashboard alerts | **DONE** (35 naye tests, 113 total pass, 80% coverage) | [docs/PHASE_3_ML.md](PHASE_3_ML.md) |
| 4 | DRF + tenant-aware ViewSets, JWT, drf-spectacular Swagger | **DONE** (46 naye tests, 166 total pass, 84% coverage) | [docs/PHASE_4_API.md](PHASE_4_API.md) |
| 5 | pytest (≥70% coverage), tenant-isolation test, Sentry, Docker + compose, GitHub Actions | **DONE** (166 tests / 84% coverage, gate 70%; Sentry + Docker + CI added) — GitHub Actions pe Python 3.11 (Django 5.2) + 3.12 (Django 6) matrix green, Docker image build + smoke test bhi green | [docs/PHASE_5_OPS.md](PHASE_5_OPS.md) |
| 6 | README rewrite (Docker, .env, multi-tenant local testing) | **DONE** | [README.md](../README.md) |

## Migrations run karne ka standard sequence (har phase)

```bash
./deploy/backup_db.sh --tag pre-phase-N        # 1. backup (wajib)
python manage.py preflight                     # 2. gate
python manage.py makemigrations <app>          # 3. review karo, blind mat chalao
python manage.py migrate --plan                # 4. kya chalega, pehle dekho
python manage.py migrate                       # 5. apply
python manage.py preflight --census            # 6. data intact hai confirm karo
```

## Data-loss guardrails (Phase 1 ke liye already decided)

- `hospital` FK pehle **nullable** add hoga → phir data migration se "Default
  Hospital" assign → phir `null=False` + index. Isse existing rows kabhi block
  nahi hongi.
- `Patient.patient_id` / `Invoice.invoice_number` abhi **globally unique** hain
  (`unique=True`). Multi-tenant mein yeh **per-hospital unique** honi chahiye, warna
  do hospitals same `PAT-2026-0001` nahi bana sakenge. Yeh change Phase 1 mein
  explicitly hoga — purani IDs bilkul waisi hi rahengi.
- `core.IDCounter` counters bhi per-hospital hone padenge (warna hospital B ke
  patient IDs hospital A se aage se shuru honge).
