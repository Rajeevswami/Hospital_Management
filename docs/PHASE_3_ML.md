# Phase 3 — AI No-Show Prediction (`ml_engine`) + Celery

Yeh phase "kaunsa patient appointment miss kar dega" predict karta hai, taaki reception
pehle se confirm call / SMS bhej sake. Do engine hain:

| Engine | Kab chalta hai | Kya use karta hai |
|---|---|---|
| **Rule-based** (`ml_engine/rules.py`) | Cold start — jab labelled data kam ho | lead time, day-of-week, slot, patient ka purana track record, contact quality |
| **scikit-learn** (`ml_engine/sklearn_model.py`) | `ML_MIN_TRAINING_ROWS` (default 250) labelled appointments ke baad | `GradientBoostingClassifier`, 21 features, joblib artifact |

Switch **automatic** hai: trained artifact available ho to sklearn, warna rules. Dono fail
ho jaayein to bhi appointment booking break nahi hoti (score `None`, request chalti rehti hai).

---

## 1. Historical data check (Phase 3 ka pehla step)

Requirement thi: "pehle historical Appointment volume check karo". Iske liye command hai:

```bash
python manage.py no_show_census                  # sab hospitals
python manage.py no_show_census --hospital acme
python manage.py no_show_census --by-month       # month-wise no-show rate
```

Sandbox ke fresh DB pe iska asli output:

```
  NO-SHOW ML DATA CENSUS
  --------------------------------------------------------------------
  Acme Hospital (slug=acme)
    total appointments : 0
    labelled (outcome) : 0  [COMPLETED=0 NO_SHOW=0 CANCELLED=0]
    no-show rate       : 0.0%
    ML threshold       : 250 rows -> NOT READY
    active model       : none (rule-based engine chalega)
    verdict            : COLD START - rule-based engine chalega (0/250 labelled rows)
```

> **Zaroori:** yeh sandbox DB khali hai, isliye yahan count 0 hai. **Production DB pe yeh
> command khud chalao** — asli verdict wahi se aayega. `manage.py preflight --census` bhi
> wahi number `ML labelled rows` ke roop mein dikhata hai.

Labelled ka matlab: status `COMPLETED` / `NO_SHOW` / `CANCELLED` wale appointments
(yani jinka outcome pata hai). `SCHEDULED` wale training data nahi hain.

---

## 2. Setup

`.env` mein naye variables (`.env.example` mein sab documented hain):

```ini
# Celery / Redis
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=                 # khali chhoda to REDIS_URL use hoga
CELERY_RESULT_BACKEND=
CELERY_TASK_ALWAYS_EAGER=True      # local: broker ke bina task sync chalega

# ML
ML_MIN_TRAINING_ROWS=250
ML_MODEL_DIR=ml_models             # git-ignored
ML_HIGH_RISK_THRESHOLD=0.65
ML_MEDIUM_RISK_THRESHOLD=0.40
ML_AUTO_SCORE_ON_CREATE=True
```

Naye packages (`requirements.txt` mein pinned): `celery==5.6.3`, `redis==8.1.0`,
`scikit-learn==1.9.1`, `joblib==1.6.0`.

Worker (production / staging pe zaroori):

```bash
celery -A hospital_system worker -l info
# connectivity check:
celery -A hospital_system call hospital_system.debug_ping
```

Local dev / tests mein `CELERY_TASK_ALWAYS_EAGER=True` rakho — broker ki zaroorat nahi,
task usi process mein turant chalta hai.

---

## 3. Flow

```
Receptionist books appointment  (POST /appointments/add/)
        │
        ▼
post_save signal (dispatch_uid=ml_engine_score_on_appointment_create)
        │   created=True only; status-update pe nahi chalta
        ▼
Celery task  ml_engine.score_appointment(appointment_id)
        │   with tenant_context(appointment.hospital)   <-- worker mein middleware nahi chalta
        ▼
ml_engine.predict.score_appointment()
        │   extract features (21)  ->  trained model? sklearn : rules
        ▼
AppointmentRisk row upsert (score, level, engine, reasons, model_version)
        │
        ▼
Dashboard panel + /ai/no-show/ page (dono `ai_no_show` feature se gated)
```

**Do safety rules jo jaan-boojh ke rakhe gaye hain:**

1. Signal poora `try/except` mein hai. Broker down ho, sklearn crash ho — appointment
   create **kabhi** fail nahi hoga. Baad mein `manage.py score_appointments` se backfill.
2. Features mein sirf **appointment se pehle ka** data jaata hai (`appointment_date__lte`,
   current appointment excluded). Warna model future dekh kar cheat karega (leakage) aur
   production pe AUC gir jaayega. Iska test hai: `test_no_leakage_from_current_appointment`.

---

## 4. Rule-based engine (cold start)

Base score `0.20`, phir factors add/subtract hote hain, final clamp `0.01–0.99`:

| Factor | Impact | Wajah |
|---|---|---|
| Lead time > 30 din | `+0.18` | Bahut pehle book kiya — bhool jaana aasan |
| Lead time 14–30 din | `+0.10` | |
| Lead time 3–7 din | `+0.02` | |
| Lead time 1–3 din | `-0.02` | |
| Same-day booking | `-0.06` | Aaj hi aana hai — pakka aayega |
| Sunday / weekend | `+0.05` / `+0.03` | Weekend pe clinics band, log plan badal dete hain |
| Friday | `+0.02` | |
| Early slot (< 9 AM) | `+0.04` | Subah jaldi pahunchna mushkil |
| Late slot (> 5 PM) | `+0.03` | |
| Patient pehle ≥50% no-show | `+0.22` | Sabse reliable signal, jab data ho |
| Patient pehle ≥25% no-show | `+0.12` | |
| Patient 3+ baar aaya, kabhi miss nahi kiya | `-0.08` | |
| Pichla appointment miss kiya tha | `+0.10` | |
| Doctor ke appointments mein ≥20% no-show | `+0.06` | Slot/clinic ka systemic issue |
| Phone number nahi hai | `+0.10` | Reminder jaayega hi nahi |
| Emergency contact nahi bhara | `+0.02` | |
| Visit ka reason nahi likha | `+0.03` | |
| Fee = 0 (free consultation) | `+0.04` | No-commitment booking |

Har impact ke saath ek human-readable `note` bhi save hota hai — UI pe "kyun high risk hai"
dikh jaata hai (`reasons` JSON column). Thresholds settings se badal sakte ho:
`>= ML_HIGH_RISK_THRESHOLD` → HIGH, `>= ML_MEDIUM_RISK_THRESHOLD` → MEDIUM, warna LOW.

---

## 5. ML model: training + switch

```bash
python manage.py train_no_show --dry-run                 # readiness dekho, train mat karo
python manage.py train_no_show                           # global model (sab hospitals ka data)
python manage.py train_no_show --hospital acme           # sirf is hospital ke data pe
python manage.py train_no_show --hospital acme --min-rows 100   # threshold override
```

Sandbox pe 300 synthetic appointments (Monday-heavy no-show pattern) ke saath **asli** output:

```
  Acme Hospital (slug=acme)
    total appointments : 300
    labelled (outcome) : 300  [COMPLETED=207 NO_SHOW=93 CANCELLED=0]
    no-show rate       : 31.0%
    ML threshold       : 250 rows -> READY
    active model       : none (rule-based engine chalega)
    verdict            : ML model train ho sakta hai (300 labelled rows)

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
    (AUC baseline 0.5 hota hai - usse kitna behtar, yeh fark batata hai)
    file            : acme/gbm_acme_20260926_123120_dd0c89.joblib
```

Dhyan do: **ROC-AUC 0.82 vs baseline 0.5** — synthetic data pe model ne pattern pakda.
Asli data pe yeh number alag hoga; `baseline_no_show_rate` ke saath compare karke dekho.

Data kam ho to command **fail nahi hoti**, clearly bolti hai:

```
  COLD START - rule-based engine chalega (0/250 labelled rows)
  Rule-based engine (ml_engine.rules) chalta rahega - koi error nahi.
```

**Model resolution order:** hospital-specific active artifact → global (`hospital=NULL`)
artifact → rules. Isliye chhote hospitals global model se fayda uthate hain aur bade
hospital apne data pe train kar sakte hain.

Retrain pe purana artifact `is_active=False` ho jaata hai aur uski joblib file delete ho
jaati hai (ek hi active version, dir phoolta nahi). Rollback chahiye to admin se purane
artifact ko `is_active` kar do — lekin uski file retrain se pehle bacha lo.

**Version string** = `gbm_<scope>_<timestamp>_<6-char random>`. Sirf timestamp kaafi nahi
tha — do trainings same second mein ho jaayein to `UNIQUE(version)` toot jaata tha
(test ne pakda, ab fixed).

### Render ka filesystem ephemeral hai

`ML_MODEL_DIR` local disk pe hai. Render/Heroku-type deployment pe restart ya naya deploy
= **model files gayab**. Do options:

1. **Release command** mein retrain: `python manage.py train_no_show` (data DB mein hai,
   isliye retrain possible hai). Simple, par har deploy pe training time.
2. Artifacts ko **object storage** (S3/GCS) mein rakho aur `ML_MODEL_DIR` ko mounted
   volume banao. Production ke liye yahi behtar hai — abhi implement nahi hua.

DB record (`NoShowModelArtifact`) rehta hai par file missing ho to `get_predictor()`
`None` deta hai aur engine chup-chaap rules pe fall back kar jaata hai (test:
`test_corrupt_model_file_falls_back_to_rules`). Koi 500 nahi.

---

## 6. Backfill (purane appointments)

Signal sirf **naye** appointment pe chalta hai. Phase 3 deploy karne ke baad ek baar:

```bash
python manage.py score_appointments                     # sab SCHEDULED
python manage.py score_appointments --hospital acme
python manage.py score_appointments --all               # COMPLETED/NO_SHOW bhi (analysis ke liye)
python manage.py score_appointments --async             # Celery queue mein daalo
```

---

## 7. UI

| Kahan | Kya |
|---|---|
| `/ai/no-show/` | Poori list: score, risk badge, engine, top-3 reasons, re-score buttons, engine status + model metrics |
| Dashboard (`/`) | "High Risk Appointments (AI)" panel — top 8 HIGH risk upcoming; doctor ko sirf apne appointments |
| Navbar | "AI Risk" link — sirf jab plan mein `ai_no_show` on ho |

Teeno `ai_no_show` feature se gated hain (abhi sirf **Scale** plan mein on). Free plan wale
hospital ko `/ai/no-show/` pe 302 → `/saas/` (upsell), dashboard pe panel dikhta hi nahi.
Templates ke liye naya context variable `feature_flags` (extra DB query nahi — Phase 2 wali
hi subscription use hoti hai).

---

## 8. Files touched

### New — `ml_engine/` app
| File | Kaam |
|---|---|
| `ml_engine/models.py` | `AppointmentRisk` (tenant-scoped), `NoShowModelArtifact` |
| `ml_engine/features.py` | 21 features + leakage-safe extraction |
| `ml_engine/rules.py` | Cold-start rule engine + `classify()` |
| `ml_engine/sklearn_model.py` | GBM training, joblib save/load, metrics, `get_predictor()` |
| `ml_engine/predict.py` | Single entry point `score_appointment()` + `readiness()` |
| `ml_engine/tasks.py` | Celery task (tenant context ke saath) |
| `ml_engine/signals.py` | `post_save` → enqueue |
| `ml_engine/apps.py` | `.signals` import |
| `ml_engine/views.py`, `urls.py` | `/ai/no-show/`, rescore, rescore-all |
| `ml_engine/admin.py` | Risk (tenant-aware) + artifact admin |
| `ml_engine/migrations/0001_initial.py` | Dono tables |
| `ml_engine/management/commands/{no_show_census,train_no_show,score_appointments}.py` | Ops commands |
| `templates/ml_engine/no_show_list.html` | AI risk page |
| `tests/test_no_show.py` | 35 tests |
| `docs/PHASE_3_ML.md` | Yeh doc |

### New — Celery wiring
`hospital_system/celery.py`, `hospital_system/__init__.py` (celery_app export)

### Modified
| File | Kya badla |
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
| `tests/test_core_flows.py` | Booking test mein risk-row assertion |
| `docs/MIGRATION_PLAN.md` | Phase 3 row → DONE |

---

## 9. Migration commands

```bash
pip install -r requirements.txt          # celery, redis, scikit-learn, joblib
python manage.py migrate                 # ml_engine.0001_initial apply hoga
python manage.py score_appointments      # purane appointments ka score backfill
python manage.py no_show_census          # apna asli data volume dekho
# jab labelled rows >= ML_MIN_TRAINING_ROWS ho jaayein:
python manage.py train_no_show --hospital <slug>
# production mein worker:
celery -A hospital_system worker -l info
```

Koi data migration nahi, koi existing table alter nahi — sirf 2 naye tables. Rollback:
`python manage.py migrate ml_engine zero`.

---

## 10. Verification

```
pytest tests/            -> 113 passed
pytest tests/ --cov=.    -> TOTAL 80%
```

`ml_engine` ka apna coverage: `features 94%`, `rules 94%`, `sklearn_model 86%`,
`predict 79%`, `tasks 95%`, `signals 88%`, `models 93%`, teeno commands 95–100%.

Test suite mein kya cover hua (35 tests):

- Rule engine: clamping, lead-time ordering, repeat no-show effect, missing phone,
  thresholds configurable
- Features: vector `FEATURE_NAMES` se match, **leakage guard**, future appointments ignore
- Async: appointment create → risk row, task result dict, missing appointment,
  `ML_AUTO_SCORE_ON_CREATE=False`, upsert (duplicate row nahi)
- Training: cold-start verdict, threshold se kam data pe refuse, train → sklearn engine
  use, retrain deactivate + file cleanup, global model fallback, corrupt file → rules
- UI/gating: free plan pe 302 → `/saas/`, scale plan pe 200, dashboard panel on/off,
  navbar link plan ke saath aata/jaata hai
- Tenant isolation: `AppointmentRisk` sirf apne hospital ka; global artifact
  (`hospital=NULL`) unscoped manager se hi milta hai
- Commands: census (+`--by-month`), train (`--dry-run`, cold-start, success, unknown
  hospital), score_appointments (sync + `--async`)
- End-to-end: HTTP `POST /appointments/add/` → risk row banti hai

Do asli bugs test suite ne pakde, dono fix kiye:
1. `NoShowModelArtifact.objects` exist hi nahi karta tha (sirf `all_objects` declare tha) →
   training crash. Ab dono managers hain.
2. Do trainings same second mein → `UNIQUE(version)` IntegrityError → version mein random
   suffix add kiya.

---

## 11. Jo verify NAHI hua (honestly)

| Cheez | Status |
|---|---|
| Production ka asli historical volume | **Unverified** — sandbox DB khali hai. `manage.py no_show_census` production pe chalao. |
| Asli data pe model accuracy | **Unverified** — 0.82 AUC synthetic data ka hai, real data ka nahi. |
| Redis broker ke saath async behaviour | **Unverified** — sandbox mein Redis nahi chala; tests `CELERY_TASK_ALWAYS_EAGER=True` pe chale. Worker deploy karke `celery call hospital_system.debug_ping` se check karo. |
| Render pe model persistence | **Unverified** — filesystem ephemeral hai, upar section 5 dekho. |
| Django 6.0 | **Unverified** — sandbox Python 3.11.2 hai, Django 6 ke liye 3.12+ chahiye. Sab kuch Django 5.2.17 pe verify hua. |

---

## 12. Manual test steps

1. `python manage.py runserver` → `acme.localhost:8000` pe admin se login.
2. Ek appointment book karo → turant `/ai/no-show/` kholo. Naya appointment score ke saath
   dikhega (eager mode mein instant; real Celery par 1–2 second baad).
3. 45 din aage ka appointment book karo → score zyada hona chahiye (lead time `+0.18`).
4. Usi patient ke 2–3 purane appointments `NO_SHOW` mark karo, naya book karo →
   "Patient pehle N/M baar nahi aaya" reason ke saath score badhega.
5. `/ai/no-show/` pe **Re-score** dabao → toast mein naya score.
6. Free plan wale hospital se `/ai/no-show/` kholo → `/saas/` pe redirect (upsell page).
7. `python manage.py no_show_census --by-month` → apna data distribution dekho.
8. Redis + worker ke saath: `CELERY_TASK_ALWAYS_EAGER=False`, `celery -A hospital_system
   worker -l info` chalao, appointment book karo, worker log mein
   `Appointment N scored: 0.45 (MEDIUM, engine=rules)` aana chahiye.

---

## 13. Phase 4 ke liye notes

- `Feature.API_ACCESS` abhi reserved hai (kisi plan mein on nahi) — Phase 4 ke DRF
  endpoints usi se gate honge.
- `AppointmentRisk` tenant-scoped hai, isliye API ViewSet bhi automatically isolate rahega
  (`AppointmentRisk.objects` = current hospital only).
- Celery task pattern (`tenant_context` + `all_objects` se fetch) Phase 4/5 ke async kaam
  ke liye template hai — copy karo.
