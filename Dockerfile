# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Hospital Management SaaS - production image
#   build : docker build -t hospital-saas .
#   run   : docker compose up   (web + postgres + redis + celery)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=hospital_system.settings

WORKDIR /app

# System deps: psycopg2 (postgres), libjpeg/zlib (Pillow), curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev libjpeg-dev zlib1g-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Pehle sirf requirements copy karo - dependency layer cache hota rahega
COPY requirements.txt .
RUN pip install -r requirements.txt

# Ab code
COPY . .

# Static files image ke andar hi collect ho jaayein (WhiteNoise serve karta hai)
# NOTE: SECRET_KEY build-time zaroori hai - isliye throwaway value use hoti hai
RUN SECRET_KEY=build-only-key DEBUG=False SAAS_ROOT_DOMAIN= \
    python manage.py collectstatic --noinput

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/staticfiles /app/media /app/ml_models /app/backups \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# entrypoint migrations chalata hai, phir CMD
ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["gunicorn", "hospital_system.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
