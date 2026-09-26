# Phase 0 — Safety Setup (SaaS Migration)

> System **live production** mein hai. Isliye Phase 0 ka kaam sirf 3 cheezein hai:
> **branch**, **backup**, **env hygiene** — plus ek `preflight` gate jo har phase
> se pehle chalaya jaayega. Koi business logic, model, ya migration is phase mein
> touch nahi hua.

---

## 0.1 Branch strategy

Is session ka saara kaam branch **`arena/01a0dd04-hospital-management`** pe ho raha hai
(Arena session isi branch se track hota hai — doosri branch pe push is session se
associate nahi hota). Content-wise yeh wahi branch hai jo tumne maangi thi, sirf naam alag hai.

**Tumhare local machine pe** `feature/saas-migration` aise banao:

```bash
git clone https://github.com/Rajeevswami/hospital-management
cd hospital-management
git checkout -b feature/saas-migration          # main (268431d) se branch
git push -u origin feature/saas-migration
```

Ya is session ki branch se seedha apni branch bana lo:

```bash
git fetch origin arena/01a0dd04-hospital-management
git checkout -b feature/saas-migration origin/arena/01a0dd04-hospital-management
```

Verified state (is sandbox mein):

```
current branch: arena/01a0dd04-hospital-management
HEAD: 268431d360f05e1b6b63cda8506d0ae22508e847   # = origin/main ka tip
worktree clean? -> 0 changed files
.env ever committed? -> NO (git log --all --diff-filter=A -- .env  = empty)
```

Rule jo aage bhi follow hoga: **har phase = alag commit(s)**, aur production deploy
tabhi jab `python manage.py preflight` green ho.

---

## 0.2 Database backup (migration se PEHLE wajib)

### A) Render (live DB) — recommended

```bash
# 1. Snapshot (sabse safe, Render ke apne storage mein)
#    Render Dashboard > PostgreSQL instance > Manual Backup
#    ya CLI se:
render services list
render pg restore-point create <pg-service-id>

# 2. Local copy bhi rakho (double safety) - external DB URL use karke:
export DATABASE_URL='postgres://USER:PASSWORD@HOST:5432/DB'   # Render > PostgreSQL > Info
./deploy/backup_db.sh --tag pre-saas-phase1
```

### B) VPS / self-hosted Postgres

```bash
cd /var/www/hospital-system
cp .env.example .env && nano .env          # DATABASE_URL bharo (agar nahi hai)
./deploy/backup_db.sh --tag pre-saas-phase1
```

### C) Plain one-liner (agar script use nahi karna)

```bash
pg_dump "$DATABASE_URL" -Fc -f "backups/hospital_db_pre_saas_$(date +%F_%H%M).dump"
pg_restore --list backups/hospital_db_pre_saas_*.dump | head    # integrity check
```

### Restore (rollback)

```bash
./deploy/backup_db.sh list                          # available dumps
./deploy/backup_db.sh restore backups/hospital_db_pre-saas-phase1_20260926_101500.dump
# 'RESTORE' type karke confirm karna padega
```

`deploy/backup_db.sh` mein kya badla:

| Pehle | Ab |
|---|---|
| DB name/user/password **hardcoded** (`hospital_db`, `hospital_user`) | `.env` / `DATABASE_URL` se padhta hai |
| Plain-text `.sql.gz` | `-Fc` custom format → integrity verify (`pg_restore --list`), selective/parallel restore |
| Restore ka koi tareeqa nahi tha | `restore` subcommand + confirmation prompt |
| Sab backups 14 din baad auto-delete | `--tag` wale backups retention se exempt (pre-migration backup kabhi khud delete nahi hoga) |
| Dump verify nahi hota tha | Dump banne ke baad table-count + size print hota hai |

**Retention / cron (VPS):**

```
0 2 * * * cd /var/www/hospital_system && ./deploy/backup_db.sh >> /var/log/hospital_system/backup.log 2>&1
```

---

## 0.3 Secrets / environment variables

`.env.example` ab **poora** template hai — Phase 1–5 ke saare variables pehle se
documented hain, taaki har phase mein settings.py ke saath env contract bhi update ho.

```bash
cp .env.example .env          # .env git-ignored hai, commit mat karna
```

| Group | Variables | Kab zaroori |
|---|---|---|
| Core Django | `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `RENDER_EXTERNAL_HOSTNAME`, `SECURE_PROXY_SSL_HEADER` | abhi |
| Database | `DATABASE_URL` | abhi |
| Multi-tenancy | `TENANCY_MODE`, `SAAS_ROOT_DOMAIN`, `TENANCY_SESSION_KEY`, `DEFAULT_HOSPITAL_NAME`, `DEFAULT_HOSPITAL_SLUG` | Phase 1 |
| Razorpay | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_WEBHOOK_URL`, `RAZORPAY_CURRENCY` | Phase 2 |
| Celery/Redis | `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CELERY_TASK_ALWAYS_EAGER` | Phase 3 |
| ML | `ML_MIN_TRAINING_ROWS`, `ML_MODEL_DIR`, `ML_HIGH_RISK_THRESHOLD` | Phase 3 |
| API/JWT | `SIMPLE_JWT_ACCESS_TOKEN_MINUTES`, `SIMPLE_JWT_REFRESH_TOKEN_DAYS` | Phase 4 |
| Sentry | `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_RELEASE` | Phase 5 |
| Email | `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | optional |
| Session | `SESSION_COOKIE_AGE`, `IDLE_TIMEOUT_SECONDS` | abhi |

Render pe yehi values **Dashboard → Service → Environment** mein daalni hain
(file upload nahi). `SECRET_KEY` generate karne ka command:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

**Abhi settings.py sirf 7 variables padhta hai** (`SECRET_KEY`, `DEBUG`,
`ALLOWED_HOSTS`, `RENDER_EXTERNAL_HOSTNAME`, `DATABASE_URL`,
`SECURE_SSL_REDIRECT`, aur `RENDER_EXTERNAL_HOSTNAME` se banne wala
`CSRF_TRUSTED_ORIGINS`). Baaki variables template mein documented hain; code unhe
us phase mein padhega jab woh feature banega — Phase 0 mein settings.py ko chheda
nahi gaya, isliye live deploy pe zero risk.

---

## 0.4 Preflight gate

```bash
python manage.py preflight              # saare checks
python manage.py preflight --census     # + row counts (Phase 3 cold-start decision)
python manage.py preflight --strict     # FAIL pe exit code 1 (CI ke liye)
python manage.py preflight --skip-secrets
```

Kya-kya check karta hai: Python ≥ 3.12, Django ≥ 6.0, `SECRET_KEY` strength
(DEBUG=False pe weak key = FAIL), `.env` git mein track to nahi, tracked files mein
hardcoded secret (Razorpay/AWS/GitHub/Sentry/Postgres-URL patterns), DB connection,
unapplied migrations, aur `--census` pe har model ka row count + ML verdict.

Is sandbox mein actual output (fresh SQLite, migrations applied):

```
  [✗] Python version: 3.11.2 - Django 6.0 ke liye 3.12+ chahiye
  [!] Django version: 5.2.17 installed, requirements.txt 6.0+ maangta hai
  [✓] SECRET_KEY: custom key set
  [✓] .env tracking: .env git mein nahi hai (sirf .env.example track hota hai)
  [✓] hardcoded secrets: 141 tracked files scan hue, koi secret nahi mila
  [✓] database: sqlite3 connected (SQLite 3.40.1)
  [✓] migrations: saari migrations applied
PREFLIGHT FAILED - 1 issue(s).
```

Python/Django wali 2 lines **is sandbox ki limitation** hain, tumhare code ka issue
nahi — neeche 0.6 dekho. Scanner ka detection verify kiya gaya: 4 dummy secrets
(fake Razorpay key, Postgres URL, GitHub token, long secret) ek temp file mein daal
ke scan kiya → **4/4 detect hue**, phir woh file hata di gayi.

---

## 0.5 Files touched in Phase 0

| File | Change |
|---|---|
| `.env.example` | Rewrite — 11 groups, Phase 1–5 ke variables documented |
| `deploy/backup_db.sh` | Rewrite — env-driven, `-Fc` dumps, restore/list mode, tagged backups |
| `core/management/commands/preflight.py` | **Naya** — safety gate command |
| `core/management/__init__.py`, `core/management/commands/__init__.py` | **Naye** (empty, package ke liye) |
| `.gitignore` | `backups/`, `*.dump`, `ml_models/`, coverage/pytest artifacts, `.env.*` |

**Migrations: koi nahi.** Phase 0 mein koi model change nahi hua, isliye
`makemigrations` / `migrate` chalane ki zaroorat nahi. Deploy bhi zaroori nahi —
par agar deploy karna ho to sirf `./deploy/backup_db.sh --tag pre-saas-phase1`
ke baad karo (naya management command add hone se live behaviour change nahi hota).

---

## 0.6 Jo cheezein is sandbox mein verify NAHI ho sakin

Honest list — inhe apni machine/production pe khud confirm karna:

1. **Python 3.12+ / Django 6.0.6 install nahi ho paya yahan** (sandbox mein Python
   3.11.2 hai; `Django==6.0.6` `Requires-Python >=3.12` maangta hai). Isliye maine
   **Django 5.2.17** pe `manage.py check`, `migrate`, aur `preflight` chalaya.
   Repo ka code 5.2 pe bhi clean chalta hai (sirf 1 pre-existing warning, neeche).
   → **Action:** Render pe `runtime.txt` add karo (`python-3.12.8`) warna default
   Python version pe `pip install -r requirements.txt` fail ho sakta hai. Repo mein
   abhi `runtime.txt` / `Procfile` / `nixpacks.toml` koi nahi hai (verified).
2. **`pg_dump` / `psql` is sandbox mein installed nahi** (aur `apt-get` ke liye root
   nahi). Isliye `backup_db.sh` ka *actual* dump path real Postgres ke against test
   nahi hua. Jo test hua: `bash -n` syntax OK, no-`DATABASE_URL` → exit 1 with
   message, missing-`pg_dump` → exit 1, aur `.env` se `DATABASE_URL` parse hoke
   script tak pahunchta hai (stub binaries ke saath confirmed).
3. **Live Render DB ka backup yahan se nahi liya ja sakta** — Render dashboard/CLI
   tumhare credentials maangta hai.

Pre-existing warning (mera change nahi, Django 5.2/axes 8.3 ka check):

```
?: (axes.W006) AXES_LOCKOUT_PARAMETERS does not contain 'ip_address'.
```

Iska matlab tumhara `AXES_LOCKOUT_PARAMETERS = ['username']` per-username lockout
karta hai — jo tumne jaan-boojh ke kiya (staff shared hospital IP use karte hain).
Phase 5 mein isko explicitly silence ya document kar denge; brute-force protection
abhi bhi kaam kar rahi hai.

---

## 0.7 Tumhe manually kya test karna hai

```bash
# 1. Preflight apni machine pe (Python 3.12 + real DATABASE_URL ke saath)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py preflight --census        # sab green aana chahiye (Python 3.12 pe)

# 2. Real DB ka backup lo aur file banti hai confirm karo
export DATABASE_URL='postgres://...your real url...'
./deploy/backup_db.sh --tag smoke-test
./deploy/backup_db.sh list
pg_restore --list backups/hospital_db_smoke-test_*.dump | tail -3   # tables dikhein

# 3. Restore drill (STAGING DB pe, production pe nahi)
createdb restore_drill
DATABASE_URL='postgres://.../restore_drill' ./deploy/backup_db.sh restore backups/hospital_db_smoke-test_*.dump

# 4. App abhi bhi normal chal rahi hai (regression check)
python manage.py runserver
#    - login (galat password 5 baar → lockout hona chahiye = axes OK)
#    - naya patient register karo (PAT-YYYY-000N banega)
#    - appointment book karo, invoice banao, PDF download karo
#    - Admin > Audit Log mein entries dikh rahi hain
```

---

## 0.8 Phase 1 se pehle jo decisions chahiye

1. **Tenant resolution**: `subdomain` (`acme.tumhara-domain.com`, DNS wildcard
   `*.domain.com` chahiye) ya `path` (`domain.com/acme/`)? Render free plan pe
   wildcard custom domain support nahi karta — path mode safe fallback hai.
2. **Existing users ka mapping**: ek user ek hi hospital se judega, ya ek user
   multiple hospitals (super-admin style) access kar sake?
3. **Celery/Redis abhi repo mein hai hi nahi** (verified: `grep -ri celery|redis`
   → 0 results). Phase 3 mein add karna padega + Render pe ek Redis instance.
   Confirm karo ki add karein.
4. **`hospital_system_delivery/` folder** repo mein ek purana duplicate copy hai
   (iska `requirements.txt` sirf `django-axes` hai, settings.py purani). Isse
   delete karein ya chhod dein?
