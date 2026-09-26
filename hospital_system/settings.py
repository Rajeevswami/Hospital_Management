"""
Django settings for hospital_system project.
Environment-driven config: dev uses SQLite fallback, production uses DATABASE_URL (PostgreSQL).
"""

import datetime as dt
from pathlib import Path

import dj_database_url
from decouple import config, Csv

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------- SECURITY ----------------
SECRET_KEY = config('SECRET_KEY', default='django-insecure-CHANGE-THIS-IN-PRODUCTION')
DEBUG = config('DEBUG', default=False, cast=bool)
# `.env` mein `ALLOWED_HOSTS=` (khaali) likha ho to python-decouple default nahi
# deta - empty string deta hai, aur default '127.0.0.1,localhost' kho jaata tha
# (phir http://127.0.0.1:8000 pe DisallowedHost 400). Blank = default maano.
_ALLOWED_HOSTS_RAW = config('ALLOWED_HOSTS', default='').strip()
ALLOWED_HOSTS = Csv()(_ALLOWED_HOSTS_RAW or '127.0.0.1,localhost')

# MULTI-TENANCY (Phase 1): subdomain scheme ke liye wildcard host chahiye,
# warna Django har tenant subdomain pe DisallowedHost (400) dega.
_SAAS_ROOT = config('SAAS_ROOT_DOMAIN', default='localhost').strip().lower()
if _SAAS_ROOT:
    # Django ka wildcard syntax: '.example.com' -> example.com AUR saare subdomains.
    # ('*.example.com' likhne se match nahi hota - leading DOT hona chahiye.)
    ALLOWED_HOSTS.append(f'.{_SAAS_ROOT}')
    if _SAAS_ROOT not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_SAAS_ROOT)

# ---------------- CSRF TRUSTED ORIGINS ----------------
# Django 4+ har POST pe browser ka `Origin` header check karta hai. HTTPS domain
# yahan listed na ho to "Origin checking failed ... does not match any trusted
# origins" (403) milta hai - login form tak submit nahi hota.
#
# Teen sources se banta hai:
#   1. CSRF_TRUSTED_ORIGINS env var (comma separated, scheme ke saath)
#   2. SAAS_ROOT_DOMAIN + uske saare subdomains (wildcard)
#   3. ALLOWED_HOSTS ke explicit entries + platform ka auto-detected hostname
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())


def _add_trusted_origin(origin):
    if origin and origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(origin)


# 2. tenant subdomains: acme.example.com pe bhi form submit hona chahiye
if _SAAS_ROOT:
    for _scheme in ('https', 'http'):
        _add_trusted_origin(f'{_scheme}://{_SAAS_ROOT}')
        _add_trusted_origin(f'{_scheme}://*.{_SAAS_ROOT}')

# 3. explicit hosts ('*' skip - uska matlab "koi bhi", trusted origin nahi bana sakte)
for _host in ALLOWED_HOSTS:
    _host = _host.strip().lstrip('.').lower()
    if not _host or _host == '*':
        continue
    for _scheme in ('https', 'http'):
        _add_trusted_origin(f'{_scheme}://{_host}')

# Auto-detect the free hosting platform's domain (Render, Railway, etc) so you
# don't need to manually edit this every time the platform assigns a new URL.
RENDER_HOSTNAME = config('RENDER_EXTERNAL_HOSTNAME', default='')
if RENDER_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_HOSTNAME)
    _add_trusted_origin(f'https://{RENDER_HOSTNAME}')

# Tells Django to trust the "X-Forwarded-Proto" header from Render/Railway's
# reverse proxy - without this, SECURE_SSL_REDIRECT causes an infinite redirect loop.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ---------------- APPS ----------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'crispy_forms',
    'crispy_bootstrap5',
    'axes',  # brute-force login protection
    # Phase 4 - REST API
    'rest_framework',
    'drf_spectacular',

    # Local apps
    'tenants',        # Phase 1: multi-tenancy (Hospital model) - sabse pehle load ho
    'subscriptions',  # Phase 2: plans + Razorpay recurring billing
    'ml_engine',      # Phase 3: no-show prediction
    'api',            # Phase 4: DRF + JWT + Swagger
    'accounts',
    'patients',
    'doctors',
    'appointments',
    'pharmacy',
    'billing',
    'wards',
    'dashboard',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves static files fast in production
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'tenants.middleware.TenantMiddleware',  # Phase 1: subdomain -> Hospital resolve (auth ke baad, axes se pehle)
    'axes.middleware.AxesMiddleware',  # must come after AuthenticationMiddleware
    'core.middleware.AuditContextMiddleware',  # makes current user/IP available to signal handlers
    'core.middleware.IdleSessionTimeoutMiddleware',  # auto-logout after inactivity
    'core.middleware.SecurityHeadersMiddleware',  # extra hardening headers
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'hospital_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # Phase 2: har template ko current plan / feature flags milte hain
                'subscriptions.context_processors.subscription',
            ],
        },
    },
]

WSGI_APPLICATION = 'hospital_system.wsgi.application'

# ---------------- DATABASE ----------------
# Local dev (no .env DATABASE_URL set) -> SQLite, fast to start.
# Production -> set DATABASE_URL=postgres://user:pass@host:port/dbname
#
# NOTE: `.env` mein `DATABASE_URL=` (khaali) likha ho to python-decouple
# default NAHI deta - empty string wapas deta hai, aur dj_database_url usse
# `UnknownSchemeError: Scheme '://'` pe phat jaata tha. Isliye blank/whitespace
# ko bhi "SQLite use karo" maana jaata hai, taaki fresh checkout `cp
# .env.example .env` ke baad seedha chal jaaye.
_DEFAULT_DB_URL = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
_DB_URL = config('DATABASE_URL', default='').strip() or _DEFAULT_DB_URL
if '://' not in _DB_URL:
    raise ImproperlyConfigured(
        f"DATABASE_URL galat hai: {_DB_URL!r}. Expected format: "
        "postgres://user:password@host:5432/dbname (ya blank chhod do SQLite ke liye)."
    )
DATABASES = {
    'default': dj_database_url.parse(
        _DB_URL,
        conn_max_age=600,   # persistent connections -> faster requests, no reconnect lag
        conn_health_checks=True,
    )
}

# ---------------- AUTH ----------------
AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'dashboard:home'
LOGOUT_REDIRECT_URL = 'accounts:login'

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',  # checks lockout BEFORE password check
    'tenants.backends.TenantModelBackend',  # Phase 1: sirf current tenant ke andar authenticate
    # NOTE: plain ModelBackend YAHAN MAT DALNA - woh username se global lookup karta
    # hai aur doosre hospital ka user authenticate kar deta. Yeh wrapper tenant
    # active hone pe khud ko disable kar deta hai.
    'tenants.backends.TenantAwareModelBackend',  # sirf tab jab koi tenant active na ho
]

# ---------------- MULTI-TENANCY (Phase 1) ----------------
# 'subdomain' -> acme.SAAS_ROOT_DOMAIN ; tenant resolve host ke pehle label se hota hai
TENANCY_MODE = config('TENANCY_MODE', default='subdomain')
# DNS mein *.SAAS_ROOT_DOMAIN wildcard record zaroori hai (production mein)
SAAS_ROOT_DOMAIN = config('SAAS_ROOT_DOMAIN', default='localhost').strip().lower()
# Session-based fallback ke liye key (subdomain na mile ya direct IP se aayein)
TENANCY_SESSION_KEY = config('TENANCY_SESSION_KEY', default='hospital_slug')
# Root domain pe bina tenant ke request aaye to login page pe bhejo (data leak nahi)
TENANT_REQUIRED = config('TENANT_REQUIRED', default=True, cast=bool)
# In paths pe tenant zaroori nahi
TENANT_EXEMPT_PATHS = tuple(
    filter(None, config('TENANT_EXEMPT_PATHS', default='/accounts/login/,/admin/login/,/healthz,/saas/webhook/', cast=Csv()))
)
# Phase 1 data migration isi hospital ko bana ke purana saara data isse assign karti hai
DEFAULT_HOSPITAL_NAME = config('DEFAULT_HOSPITAL_NAME', default='Default Hospital')
DEFAULT_HOSPITAL_SLUG = config('DEFAULT_HOSPITAL_SLUG', default='default')

# ---------------- RAZORPAY (Phase 2 - recurring subscriptions) ----------------
# Test-mode keys: Dashboard > Settings > API Keys. LIVE keys sirf production env mein.
RAZORPAY_KEY_ID = config('RAZORPAY_KEY_ID', default='')
RAZORPAY_KEY_SECRET = config('RAZORPAY_KEY_SECRET', default='')
# Webhook secret: Dashboard > Webhooks > Add > "Secret" (signature verify ke liye)
RAZORPAY_WEBHOOK_SECRET = config('RAZORPAY_WEBHOOK_SECRET', default='')
RAZORPAY_CURRENCY = config('RAZORPAY_CURRENCY', default='INR')

# Payment fail hone ke baad kitne din tak access chalu rahe (hospital ka kaam
# ek failed payment se band kar dena safe nahi).
SUBSCRIPTION_GRACE_DAYS = config('SUBSCRIPTION_GRACE_DAYS', default=3, cast=int)

# ---------------- CELERY / REDIS (Phase 3) ----------------
# Local dev: CELERY_TASK_ALWAYS_EAGER=True -> broker ke bina tasks sync chalte hain.
# Production: REDIS_URL set karo aur worker chalao (celery -A hospital_system worker).
REDIS_URL = config('REDIS_URL', default='')
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=REDIS_URL) or 'memory://'
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default=REDIS_URL) or 'cache+memory://'
CELERY_TASK_ALWAYS_EAGER = config('CELERY_TASK_ALWAYS_EAGER', default=False, cast=bool)
CELERY_TASK_EAGER_PROPAGATES = True   # eager mode mein exception chhupao mat (tests ke liye zaroori)
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = 'Asia/Kolkata'   # TIME_ZONE isi file mein niche define hota hai
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# ---------------- ML / NO-SHOW PREDICTION (Phase 3) ----------------
# Isse kam labelled appointments -> rule-based engine (cold start fallback)
ML_MIN_TRAINING_ROWS = config('ML_MIN_TRAINING_ROWS', default=250, cast=int)
# Trained joblib files kahan rahengi (git-ignored). NOTE: Render ka filesystem
# ephemeral hai - deploy ke baad retrain karo ya object storage use karo.
ML_MODEL_DIR = config('ML_MODEL_DIR', default=str(BASE_DIR / 'ml_models'))
ML_HIGH_RISK_THRESHOLD = config('ML_HIGH_RISK_THRESHOLD', default=0.65, cast=float)
ML_MEDIUM_RISK_THRESHOLD = config('ML_MEDIUM_RISK_THRESHOLD', default=0.40, cast=float)
# Appointment create hote hi risk score compute karein (False = sirf manual command se)
ML_AUTO_SCORE_ON_CREATE = config('ML_AUTO_SCORE_ON_CREATE', default=True, cast=bool)

# ---------------- REST API (Phase 4) ----------------
# JWT auth. Session auth bhi on hai taaki Swagger UI se bina token ke try kar sako.
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
        'api.permissions.IsTenantMember',   # cross-tenant request -> 403
    ),
    'DEFAULT_PAGINATION_CLASS': 'api.pagination.StandardPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_FILTER_BACKENDS': (
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ),
    'EXCEPTION_HANDLER': 'api.exceptions.api_exception_handler',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': dt.timedelta(
        minutes=config('SIMPLE_JWT_ACCESS_TOKEN_MINUTES', default=60, cast=int)),
    'REFRESH_TOKEN_LIFETIME': dt.timedelta(
        days=config('SIMPLE_JWT_REFRESH_TOKEN_DAYS', default=7, cast=int)),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,   # token_blacklist app install nahi hai
    'UPDATE_LAST_LOGIN': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    # Token claims (hospital slug + role) custom serializer se aate hain -
    # convenience ke liye; authorization hamesha SERVER pe check hoti hai.
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Hospital Management SaaS API',
    'DESCRIPTION': (
        'Multi-tenant hospital management API.\n\n'
        '**Tenant:** request `<hospital>.<root-domain>` pe bhejo '
        '(jaise `acme.example.com/api/patients/`), ya `X-Hospital-Slug: acme` header.\n\n'
        '**Auth:** `POST /api/token/` se JWT lo, phir `Authorization: Bearer <token>`.\n\n'
        'Data endpoints ke liye plan mein `api_access` feature zaroori hai (Scale plan).'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SCHEMA_PATH_PREFIX': '/api',
    'COMPONENT_SPLIT_REQUEST': True,
    'SERVE_PERMISSIONS': ['rest_framework.permissions.AllowAny'],
    'SWAGGER_UI_SETTINGS': {'persistAuthorization': True, 'displayRequestDuration': True},
}

# ---------------- SENTRY (Phase 5) ----------------
# DSN khali ho to init hi nahi hota - local dev bilkul unaffected.
SENTRY_DSN = config('SENTRY_DSN', default='')
SENTRY_ENVIRONMENT = config('SENTRY_ENVIRONMENT', default='local')
SENTRY_TRACES_SAMPLE_RATE = config('SENTRY_TRACES_SAMPLE_RATE', default=0.1, cast=float)

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        # Hospital data hai - user ka PII (email/IP) Sentry mein mat bhejo
        send_default_pii=False,
        release=config('SENTRY_RELEASE', default='') or None,
    )

# ---------------- BRUTE-FORCE LOGIN PROTECTION (django-axes) ----------------
AXES_FAILURE_LIMIT = 5            # lock after 5 wrong attempts
AXES_COOLOFF_TIME = 0.5           # unlock automatically after 30 minutes
AXES_LOCKOUT_PARAMETERS = ['username']   # lock per-username, not per-IP (staff share hospital WiFi/IP)
AXES_RESET_ON_SUCCESS = True      # successful login clears the failure counter

# ---------------- COOKIE SAMESITE ----------------
# Default 'Lax' sabse safe hai. Par jab app kisi DOOSRI site ke iframe mein khulti
# hai (preview panes, embedded dashboards), browser Lax cookies cross-site request
# mein bhejta hi nahi -> form POST pe "CSRF cookie not set" (403).
#
# Aise case mein COOKIE_SAMESITE=None karo. Django ko 'None' ke saath Secure
# zaroori hai, isliye woh bhi khud on ho jaata hai (matlab HTTPS chahiye).
COOKIE_SAMESITE = config('COOKIE_SAMESITE', default='Lax')
SESSION_COOKIE_SAMESITE = COOKIE_SAMESITE
CSRF_COOKIE_SAMESITE = COOKIE_SAMESITE
if COOKIE_SAMESITE.lower() == 'none':
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ---------------- IDLE SESSION TIMEOUT ----------------
SESSION_COOKIE_AGE = 1800          # 30 minutes
SESSION_SAVE_EVERY_REQUEST = True  # sliding expiry - resets the 30 min on activity
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
IDLE_TIMEOUT_SECONDS = 1800         # used by core.middleware.IdleSessionTimeoutMiddleware

# ---------------- I18N ----------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# ---------------- STATIC / MEDIA ----------------
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------- CRISPY FORMS ----------------
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# ---------------- PRODUCTION SECURITY (auto-enabled when DEBUG=False) ----------------
if not DEBUG:
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
