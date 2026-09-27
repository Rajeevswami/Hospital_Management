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
# If `.env` has `ALLOWED_HOSTS=` (blank), python-decouple does not return the
# default - it returns an empty string, and the default '127.0.0.1,localhost'
# was lost (then DisallowedHost 400 on http://127.0.0.1:8000). Treat blank as "use the default".
_ALLOWED_HOSTS_RAW = config('ALLOWED_HOSTS', default='').strip()
ALLOWED_HOSTS = Csv()(_ALLOWED_HOSTS_RAW or '127.0.0.1,localhost')

# MULTI-TENANCY (Phase 1): the subdomain scheme needs a wildcard host,
# otherwise Django returns DisallowedHost (400) on every tenant subdomain.
_SAAS_ROOT = config('SAAS_ROOT_DOMAIN', default='localhost').strip().lower()
if _SAAS_ROOT:
    # Django's wildcard syntax: '.example.com' -> example.com AND all subdomains.
    # (Writing '*.example.com' does not match - there must be a leading DOT.)
    ALLOWED_HOSTS.append(f'.{_SAAS_ROOT}')
    if _SAAS_ROOT not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_SAAS_ROOT)

# ---------------- CSRF TRUSTED ORIGINS ----------------
# Django 4+ checks the browser's `Origin` header on every POST. If the HTTPS
# domain is not listed here, you get "Origin checking failed ... does not match
# any trusted origins" (403) - even the login form cannot be submitted.
#
# It is built from three sources:
#   1. CSRF_TRUSTED_ORIGINS env var (comma separated, with scheme)
#   2. SAAS_ROOT_DOMAIN + all its subdomains (wildcard)
#   3. explicit ALLOWED_HOSTS entries + the platform's auto-detected hostname
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())


def _add_trusted_origin(origin):
    if origin and origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(origin)


# 2. tenant subdomains: forms must also submit on acme.example.com
if _SAAS_ROOT:
    for _scheme in ('https', 'http'):
        _add_trusted_origin(f'{_scheme}://{_SAAS_ROOT}')
        _add_trusted_origin(f'{_scheme}://*.{_SAAS_ROOT}')

# 3. explicit hosts ('*' is skipped - it means "any", which cannot be a trusted origin)
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
    'tenants',        # Phase 1: multi-tenancy (Hospital model) - must load first
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
    'tenants.middleware.TenantMiddleware',  # Phase 1: subdomain -> Hospital resolution (after auth, before axes)
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
                # Phase 2: every template receives the current plan / feature flags
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
# NOTE: if `.env` has `DATABASE_URL=` (blank), python-decouple does NOT
# return the default - it returns an empty string, and dj_database_url used to
# crash with `UnknownSchemeError: Scheme '://'`. That is why blank/whitespace
# is also treated as "use SQLite", so a fresh checkout runs right after
# `cp .env.example .env`.
_DEFAULT_DB_URL = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
_DB_URL = config('DATABASE_URL', default='').strip() or _DEFAULT_DB_URL
if '://' not in _DB_URL:
    raise ImproperlyConfigured(
        f"Invalid DATABASE_URL: {_DB_URL!r}. Expected format: "
        "postgres://user:password@host:5432/dbname (or leave it blank for SQLite)."
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
    'tenants.backends.TenantModelBackend',  # Phase 1: authenticates only within the current tenant
    # NOTE: do NOT add plain ModelBackend HERE - it does a global lookup by
    # username and would authenticate a user from another hospital. This wrapper
    # disables itself when a tenant is active.
    'tenants.backends.TenantAwareModelBackend',  # only when no tenant is active
]

# ---------------- MULTI-TENANCY (Phase 1) ----------------
# 'subdomain' -> acme.SAAS_ROOT_DOMAIN ; the tenant is resolved from the first label of the host
TENANCY_MODE = config('TENANCY_MODE', default='subdomain')
# A *.SAAS_ROOT_DOMAIN wildcard DNS record is required (in production)
SAAS_ROOT_DOMAIN = config('SAAS_ROOT_DOMAIN', default='localhost').strip().lower()
# Key for the session-based fallback (when the subdomain is missing or the client uses a direct IP)
TENANCY_SESSION_KEY = config('TENANCY_SESSION_KEY', default='hospital_slug')
# Send tenant-less requests on the root domain to the login page (no data leak)
TENANT_REQUIRED = config('TENANT_REQUIRED', default=True, cast=bool)
# Tenant is not required on these paths
TENANT_EXEMPT_PATHS = tuple(
    filter(None, config('TENANT_EXEMPT_PATHS', default='/accounts/login/,/admin/login/,/healthz,/saas/webhook/', cast=Csv()))
)
# The Phase 1 data migration creates this hospital and assigns all pre-existing data to it
DEFAULT_HOSPITAL_NAME = config('DEFAULT_HOSPITAL_NAME', default='Default Hospital')
DEFAULT_HOSPITAL_SLUG = config('DEFAULT_HOSPITAL_SLUG', default='default')

# ---------------- RAZORPAY (Phase 2 - recurring subscriptions) ----------------
# Test-mode keys: Dashboard > Settings > API Keys. LIVE keys only in the production env.
RAZORPAY_KEY_ID = config('RAZORPAY_KEY_ID', default='')
RAZORPAY_KEY_SECRET = config('RAZORPAY_KEY_SECRET', default='')
# Webhook secret: Dashboard > Webhooks > Add > "Secret" (for signature verification)
RAZORPAY_WEBHOOK_SECRET = config('RAZORPAY_WEBHOOK_SECRET', default='')
RAZORPAY_CURRENCY = config('RAZORPAY_CURRENCY', default='INR')

# How many days access continues after a failed payment (stopping a hospital's
# work over a single failed payment is not safe).
SUBSCRIPTION_GRACE_DAYS = config('SUBSCRIPTION_GRACE_DAYS', default=3, cast=int)

# ---------------- CELERY / REDIS (Phase 3) ----------------
# Local dev: CELERY_TASK_ALWAYS_EAGER=True -> tasks run synchronously without a broker.
# Production: set REDIS_URL and run a worker (celery -A hospital_system worker).
REDIS_URL = config('REDIS_URL', default='')
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=REDIS_URL) or 'memory://'
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default=REDIS_URL) or 'cache+memory://'
CELERY_TASK_ALWAYS_EAGER = config('CELERY_TASK_ALWAYS_EAGER', default=False, cast=bool)
CELERY_TASK_EAGER_PROPAGATES = True   # do not swallow exceptions in eager mode (required for tests)
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = 'Asia/Kolkata'   # TIME_ZONE is defined below in this file
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# ---------------- ML / NO-SHOW PREDICTION (Phase 3) ----------------
# Fewer labelled appointments than this -> rule-based engine (cold start fallback)
ML_MIN_TRAINING_ROWS = config('ML_MIN_TRAINING_ROWS', default=250, cast=int)
# Where the trained joblib files live (git-ignored). NOTE: Render's filesystem
# is ephemeral - retrain after deploy or use object storage.
ML_MODEL_DIR = config('ML_MODEL_DIR', default=str(BASE_DIR / 'ml_models'))
ML_HIGH_RISK_THRESHOLD = config('ML_HIGH_RISK_THRESHOLD', default=0.65, cast=float)
ML_MEDIUM_RISK_THRESHOLD = config('ML_MEDIUM_RISK_THRESHOLD', default=0.40, cast=float)
# Compute the risk score as soon as an appointment is created (False = only via the manual command)
ML_AUTO_SCORE_ON_CREATE = config('ML_AUTO_SCORE_ON_CREATE', default=True, cast=bool)

# ---------------- REST API (Phase 4) ----------------
# JWT auth. Session auth is also on so you can try the API from the Swagger UI without a token.
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
    'BLACKLIST_AFTER_ROTATION': False,   # the token_blacklist app is not installed
    'UPDATE_LAST_LOGIN': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    # Token claims (hospital slug + role) come from a custom serializer -
    # for convenience only; authorization is always checked on the SERVER.
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Hospital Management SaaS API',
    'DESCRIPTION': (
        'Multi-tenant hospital management API.\n\n'
        '**Tenant:** send the request to `<hospital>.<root-domain>` '
        '(like `acme.example.com/api/patients/`), or use the `X-Hospital-Slug: acme` header.\n\n'
        '**Auth:** get a JWT from `POST /api/token/`, then send `Authorization: Bearer <token>`.\n\n'
        'Data endpoints require the `api_access` feature in the plan (Scale plan).'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SCHEMA_PATH_PREFIX': '/api',
    'COMPONENT_SPLIT_REQUEST': True,
    'SERVE_PERMISSIONS': ['rest_framework.permissions.AllowAny'],
    'SWAGGER_UI_SETTINGS': {'persistAuthorization': True, 'displayRequestDuration': True},
}

# ---------------- SENTRY (Phase 5) ----------------
# If the DSN is empty, init does not happen at all - local dev is completely unaffected.
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
        # This is hospital data - do not send user PII (email/IP) to Sentry
        send_default_pii=False,
        release=config('SENTRY_RELEASE', default='') or None,
    )

# ---------------- BRUTE-FORCE LOGIN PROTECTION (django-axes) ----------------
AXES_FAILURE_LIMIT = 5            # lock after 5 wrong attempts
AXES_COOLOFF_TIME = 0.5           # unlock automatically after 30 minutes
AXES_LOCKOUT_PARAMETERS = ['username']   # lock per-username, not per-IP (staff share hospital WiFi/IP)
AXES_RESET_ON_SUCCESS = True      # successful login clears the failure counter

# ---------------- COOKIE SAMESITE ----------------
# The default 'Lax' is the safest. But when the app opens inside ANOTHER site's
# iframe (preview panes, embedded dashboards), the browser never sends Lax
# cookies with cross-site requests -> form POSTs fail with "CSRF cookie not set"
# (403).
#
# In such cases set COOKIE_SAMESITE=None. Django requires Secure with 'None',
# so that is turned on automatically too (meaning HTTPS is required).
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
