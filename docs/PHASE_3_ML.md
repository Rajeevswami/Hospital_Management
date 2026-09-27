# Phase 3 — AI No-Show Prediction (`ml_engine`) + Celery

This phase predicts "which patient will miss their appointment", so reception can
send a confirmation call / SMS in advance. There are two engines:

| Engine | When it runs | What it uses |
|---|---|---|
| **Rule-based** (`ml_engine/rules.py`) | Cold start — when labelled data is scarce | lead time, day-of-week, slot, the patient's past track record, contact quality |
| **scikit-learn** (`ml_engine/sklearn_model.py`) | after `ML_MIN_TRAINING_ROWS` (default 250) labelled appointments | `GradientBoostingClassifier`, 21 features, joblib artifact |

The switch is **automatic**: if a trained artifact is available → sklearn, else
rules. If both fail, appointment booking still does not break (score is `None`,
the request keeps going).

---

## 1. Historical data check (first step of Phase 3)

The requirement was: "first check the historical Appointment volume". The
command for that:

```bash
python manage.py no_show_census                  # all hospitals
python manage.py no_show_census --hospital acme
python manage.py no_show_census --by-month       # month-wise no-show rate
```

Its actual output on the sandbox's fresh DB:

```
  NO-SHOW ML DATA CENSUS
  --------------------------------------------------------------------
  Acme Hospital (slug=acme)
    total appointments : 0
    labelled (outcome) : 0  [COMPLETED=0 NO_SHOW=0 CANCELLED=0]
    no-show rate       : 0.0%
    ML threshold       : 250 rows -> NOT READY
    active model       : none (rule-based engine will run)
    verdict            : COLD START - rule-based engine will run (0/250 labelled rows)
```

> **Important:** this sandbox DB is empty, so the count is 0 here. **Run this
> command against the production DB yourself** — the real verdict comes from
> there. `manage.py preflight --census` also shows the same number as
> `ML labelled rows`.

"Labelled" means: appointments with status `COMPLETED` / `NO_SHOW` / `CANCELLED`
(i.e. whose outcome is known). `SCHEDULED` ones are not training data.

---

## 2. Setup

New variables in `.env` (all documented in `.env.example`):

```ini
# Celery / Redis
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=                 # if left empty, REDIS_URL is used
CELERY_RESULT_BACKEND=
CELERY_TASK_ALWAYS_EAGER=True      # local: tasks run synchronously without a broker

# ML
ML_MIN_TRAINING_ROWS=250
ML_MODEL_DIR=ml_models             # git-ignored
ML_HIGH_RISK_THRESHOLD=0.65
ML_MEDIUM_RISK_THRESHOLD=0.40
ML_AUTO_SCORE_ON_CREATE=True
```

New packages (pinned in `requirements.txt`): `celery==5.6.3`, `redis==8.1.0`,
`scikit-learn==1.9.1`, `joblib==1.6.0`.

Worker (required on production / staging):

```bash
celery -A hospital_system worker -l info
# connectivity check:
celery -A hospital_system call hospital_system.debug_ping
```

Keep `CELERY_TASK_ALWAYS_EAGER=True` in local dev / tests — no broker needed, the
task runs immediately in the same process.

---

## 3. Flow

```
Receptionist books appointment  (POST /appointments/add/)
        │
        ▼
post_save signal (dispatch_uid=ml_engine_score_on_appointment_create)
        │   created=True only; it does not run on status updates
        ▼
Celery task  ml_engine.score_appointment(appointment_id)
        │   with tenant_context(appointment.hospital)   <-- no middleware in the worker
        ▼
ml_engine.predict.score_appointment()
        │   extract features (21)  ->  trained model? sklearn : rules
        ▼
AppointmentRisk row upsert (score, level, engine, reasons, model_version)
        │
        ▼
Dashboard panel + /ai/no-show/ page (both gated by the `ai_no_show` feature)
```

**Two safety rules kept deliberately:**

1. The signal is wrapped fully in `try/except`. Whether the broker is down or
   sklearn crashes — appointment creation will **never** fail. Backfill later
   with `manage.py score_appointments`.
2. Features only include data from **before the appointment**
   (`appointment_date__lte`, current appointment excluded). Otherwise the model
   would cheat by seeing the future (leakage) and production AUC would drop.
   There is a test for this: `test_no_leakage_from_current_appointment`.

---

## 4. Rule-based engine (cold start)

Base score `0.20`, then factors add/subtract, final clamp `0.01–0.99`:

| Factor | Impact | Reason |
|---|---|---|
| Lead time > 30 days | `+0.18` | Booked very far in advance — easy to forget |
| Lead time 14–30 days | `+0.10` | |
| Lead time 3–7 days | `+0.02` | |
| Lead time 1–3 days | `-0.02` | |
| Same-day booking | `-0.06` | Coming today — they will definitely show up |
| Sunday / weekend | `+0.05` / `+0.03` | Clinics are closed on weekends, people change plans |
| Friday | `+0.02` | |
| Early slot (< 9 AM) | `+0.04` | Hard to arrive early in the morning |
| Late slot (> 5 PM) | `+0.03` | |
| Patient previously ≥50% no-show | `+0.22` | Most reliable signal, when data exists |
| Patient previously ≥25% no-show | `+0.12` | |
| Patient came 3+ times, never missed | `-0.08` | |
| Missed the previous appointment | `+0.10` | |
| ≥20% no-show among the doctor's appointments | `+0.06` | Systemic slot/clinic issue |
| No phone number | `+0.10` | A reminder cannot be sent at all |
| Emergency contact not filled in | `+0.02` | |
| Visit reason not written | `+0.03` | |
| Fee = 0 (free consultation) | `+0.04` | No-commitment booking |

Each impact also saves a human-readable `note` — the UI shows "why is this high
risk" (`reasons` JSON column). Thresholds can be changed via settings:
`>= ML_HIGH_RISK_THRESHOLD` → HIGH, `>= ML_MEDIUM_RISK_THRESHOLD` → MEDIUM, else
LOW.

---

## 5. ML model: training + switch

```bash
python manage.py train_no_show --dry-run                 # view readiness, do not train
python manage.py train_no_show                           # global model (all hospitals' data)
python manage.py train_no_show --hospital acme           # only on this hospital's data
python manage.py train_no_show --hospital acme --min-rows 100   # override the threshold
```

**Actual** output in the sandbox with 300 synthetic appointments (Monday-heavy
no-show pattern):

```
  Acme Hospital (slug=acme)
    total appointments : 300
    labelled (outcome) : 300  [COMPLETED=207 NO_SHOW=93 CANCELLED=0]
    no-show rate       : 31.0%
    ML threshold       : 250 rows -> READY
    active model       : none (rule-based engine will run)
    verdict            : ML model can be trained (300 labelled rows)

  scope           : acme
  labelled rows   : 300
  no-show rows    : 93 (31.0%)
  required        : 250

  TRAINED: gbm_acme_20260926_123120_dd0c89
    rows            : 300
    ROC-AUC         : 0.8241
    precision@0.5   : 0.6875
    recall@0.5      : 0.5789
    baseline rate   : 0.3167
    (the AUC baseline is 0.5 - this difference shows how much better it is)
    file            : acme/gbm_acme_20260926_123120_dd0c89.joblib
```

Note: **ROC-AUC 0.82 vs baseline 0.5** — on synthetic data the model caught the
pattern. On real data these numbers will differ; compare against
`baseline_no_show_rate`.

When data is scarce the command **does not fail**, it says so clearly:

```
  COLD START - rule-based engine will run (0/250 labelled rows)
  The rule-based engine (ml_engine.rules) keeps running - no error.
```

**Model resolution order:** hospital-specific active artifact → global
(`hospital=NULL`) artifact → rules. So small hospitals benefit from the global
model while large hospitals can train on their own data.

On retrain, the old artifact becomes `is_active=False` and its joblib file is
deleted (only one active version, the dir does not fill up). If you need a
rollback, mark an old artifact `is_active` from admin — but save its file first,
before the retrain.

**Version string** = `gbm_<scope>_<timestamp>_<6-char random>`. A timestamp alone
was not enough — two trainings in the same second broke `UNIQUE(version)` (a test
caught it, now fixed).

### Render's filesystem is ephemeral

`ML_MODEL_DIR` is on local disk. On Render/Heroku-type deployments a restart or a
new deploy = **model files gone**. Two options:

1. Retrain in the **release command**: `python manage.py train_no_show` (the data
   is in the DB, so retraining is possible). Simple, but training time on every
   deploy.
2. Keep artifacts in **object storage** (S3/GCS) and make `ML_MODEL_DIR` a
   mounted volume. This is better for production — not implemented yet.

If the DB record (`NoShowModelArtifact`) remains but the file is missing,
`get_predictor()` returns `None` and the engine quietly falls back to rules
(test: `test_corrupt_model_file_falls_back_to_rules`). No 500s.

---

## 6. Backfill (old appointments)

The signal only runs on **new** appointments. Once after deploying Phase 3:

```bash
python manage.py score_appointments                     # all SCHEDULED
python manage.py score_appointments --hospital acme
python manage.py score_appointments --all               # COMPLETED/NO_SHOW too (for analysis)
python manage.py score_appointments --async             # push into the Celery queue
```

---

## 7. UI

| Where | What |
|---|---|
| `/ai/no-show/` | Full list: score, risk badge, engine, top-3 reasons, re-score buttons, engine status + model metrics |
| Dashboard (`/`) | "High Risk Appointments (AI)" panel — top 8 HIGH-risk upcoming; a doctor sees only their own appointments |
| Navbar | "AI Risk" link — only when the plan has `ai_no_show` on |

All three are gated by the `ai_no_show` feature (currently on only in the
**Scale** plan). For a hospital on the Free plan, `/ai/no-show/` 302s to
`/saas/` (upsell), and the dashboard panel does not appear at all. A new context
variable `feature_flags` for templates (no extra DB query — it reuses the Phase 2
subscription).

---

## 8. Files touched

### New — `ml_engine/` app
| File | Purpose |
|---|---|
| `ml_engine/models.py` | `AppointmentRisk` (tenant-scoped), `NoShowModelArtifact` |
| `ml_engine/features.py` | 21 features + leakage-safe extraction |
| `ml_engine/rules.py` | Cold-start rule engine + `classify()` |
| `ml_engine/sklearn_model.py` | GBM training, joblib save/load, metrics, `get_predictor()` |
| `ml_engine/predict.py` | Single entry point `score_appointment()` + `readiness()` |
| `ml_engine/tasks.py` | Celery task (with tenant context) |
| `ml_engine/signals.py` | `post_save` → enqueue |
| `ml_engine/apps.py` | `.signals` import |
| `ml_engine/views.py`, `urls.py` | `/ai/no-show/`, rescore, rescore-all |
| `ml_engine/admin.py` | Risk (tenant-aware) + artifact admin |
| `ml_engine/migrations/0001_initial.py` | Both tables |
| `ml_engine/management/commands/{no_show_census,train_no_show,score_appointments}.py` | Ops commands |
| `templates/ml_engine/no_show_list.html` | AI risk page |
| `tests/test_no_show.py` | 35 tests |
| `docs/PHASE_3_ML.md` | This doc |

### New — Celery wiring
`hospital_system/celery.py`, `hospital_system/__init__.py` (celery_app export)

### Modified
| File | What changed |
|---|---|
| `hospital_system/settings.py` | `ml_engine` app, `CELERY_*`, `ML_*` settings |
| `hospital_system/urls.py` | `path('ai/', include('ml_engine.urls'))` |
| `dashboard/views.py` | `ai_no_show_enabled` + `high_risk_appointments` context |
| `templates/dashboard/home.html` | High-risk panel |
| `templates/base.html` | Navbar "AI Risk" link |
| `subscriptions/context_processors.py` | `feature_flags` dict |
| `requirements.txt` | celery, redis, scikit-learn, joblib |
| `.env.example` | Celery + ML section |
| `tests/settings_test.py` | Eager Celery, temp `ML_MODEL_DIR`, `ML_MIN_TRAINING_ROWS=40` |
| `tests/test_core_flows.py` | risk-row assertion in the booking test |
| `docs/MIGRATION_PLAN.md` | Phase 3 row → DONE |

---

## 9. Migration commands

```bash
pip install -r requirements.txt          # celery, redis, scikit-learn, joblib
python manage.py migrate                 # applies ml_engine.0001_initial
python manage.py score_appointments      # backfill scores for old appointments
python manage.py no_show_census          # see your real data volume
# once labelled rows >= ML_MIN_TRAINING_ROWS:
python manage.py train_no_show --hospital <slug>
# worker in production:
celery -A hospital_system worker -l info
```

No data migration, no existing table altered — only 2 new tables. Rollback:
`python manage.py migrate ml_engine zero`.

---

## 10. Verification

```
pytest tests/            -> 113 passed
pytest tests/ --cov=.    -> TOTAL 80%
```

`ml_engine`'s own coverage: `features 94%`, `rules 94%`, `sklearn_model 86%`,
`predict 79%`, `tasks 95%`, `signals 88%`, `models 93%`, all three commands
95–100%.

What the test suite covers (35 tests):

- Rule engine: clamping, lead-time ordering, repeat no-show effect, missing phone,
  configurable thresholds
- Features: vector matches `FEATURE_NAMES`, **leakage guard**, future appointments
  ignored
- Async: appointment create → risk row, task result dict, missing appointment,
  `ML_AUTO_SCORE_ON_CREATE=False`, upsert (no duplicate rows)
- Training: cold-start verdict, refuse on insufficient data, train → uses sklearn
  engine, retrain deactivates + file cleanup, global model fallback, corrupt file
  → rules
- UI/gating: free plan → 302 to `/saas/`, scale plan → 200, dashboard panel
  on/off, navbar link appears/disappears with the plan
- Tenant isolation: `AppointmentRisk` only for its own hospital; the global
  artifact (`hospital=NULL`) is only found via the unscoped manager
- Commands: census (+`--by-month`), train (`--dry-run`, cold-start, success,
  unknown hospital), score_appointments (sync + `--async`)
- End-to-end: HTTP `POST /appointments/add/` → a risk row is created

Two real bugs the test suite caught, both fixed:
1. `NoShowModelArtifact.objects` did not exist at all (only `all_objects` was
   declared) → training crashed. Now both managers exist.
2. Two trainings in the same second → `UNIQUE(version)` IntegrityError → added a
   random suffix to the version.

---

## 11. What was NOT verified (honestly)

| Item | Status |
|---|---|
| Real historical production volume | **Unverified** — the sandbox DB is empty. Run `manage.py no_show_census` on production. |
| Model accuracy on real data | **Unverified** — the 0.82 AUC is on synthetic data, not real data. |
| Async behaviour with a Redis broker | **Unverified** — Redis did not run in the sandbox; tests ran with `CELERY_TASK_ALWAYS_EAGER=True`. Deploy a worker and check with `celery call hospital_system.debug_ping`. |
| Model persistence on Render | **Unverified** — the filesystem is ephemeral, see section 5 above. |
| Django 6.0 | **Unverified** — the sandbox has Python 3.11.2; Django 6 needs 3.12+. Everything was verified on Django 5.2.17. |

---

## 12. Manual test steps

1. `python manage.py runserver` → log in as admin at `acme.localhost:8000`.
2. Book an appointment → open `/ai/no-show/` right away. The new appointment
   should appear with a score (instant in eager mode; 1–2 seconds with real
   Celery).
3. Book an appointment 45 days ahead → the score should be higher (lead time
   `+0.18`).
4. Mark 2–3 of the same patient's old appointments as `NO_SHOW`, book a new one
   → the score should rise with the reason "Patient did not come N/M times
   before".
5. Press **Re-score** on `/ai/no-show/` → the new score in the toast.
6. Open `/ai/no-show/` from a hospital on the Free plan → redirect to `/saas/`
   (upsell page).
7. `python manage.py no_show_census --by-month` → view your data distribution.
8. With Redis + worker: set `CELERY_TASK_ALWAYS_EAGER=False`, run
   `celery -A hospital_system worker -l info`, book an appointment, and the
   worker log should show
   `Appointment N scored: 0.45 (MEDIUM, engine=rules)`.

---

## 13. Notes for Phase 4

- `Feature.API_ACCESS` is currently reserved (not on in any plan) — the Phase 4
  DRF endpoints will be gated by it.
- `AppointmentRisk` is tenant-scoped, so the API ViewSet is automatically
  isolated too (`AppointmentRisk.objects` = current hospital only).
- The Celery task pattern (`tenant_context` + fetch via `all_objects`) is the
  template for async work in Phase 4/5 — copy it.
