#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Container start hone se pehle:
#   1. Postgres ke ready hone ka wait (compose healthcheck ke bharose na rahein)
#   2. migrations apply
#   3. static files (volume mount ke case mein dobara collect)
#   4. phir CMD chalao (gunicorn / celery worker)
#
# Celery worker ke liye migrations skip karne ka option: SKIP_MIGRATIONS=1
# ---------------------------------------------------------------------------
set -euo pipefail

echo "[entrypoint] waiting for database..."
python - <<'PY'
import os, time
import psycopg2
from decouple import config
import dj_database_url

url = config("DATABASE_URL", default="")
if not url or url.startswith("sqlite"):
    print("[entrypoint] sqlite - koi wait nahi")
    raise SystemExit(0)

conf = dj_database_url.parse(url)
for attempt in range(30):
    try:
        psycopg2.connect(
            dbname=conf["NAME"], user=conf["USER"], password=conf["PASSWORD"],
            host=conf["HOST"], port=conf["PORT"],
        ).close()
        print("[entrypoint] database ready")
        raise SystemExit(0)
    except psycopg2.OperationalError:
        time.sleep(2)
raise SystemExit("[entrypoint] database ready nahi hua (60s) - exit")
PY

if [ "${SKIP_MIGRATIONS:-0}" != "1" ] && [ "${1:-}" != "celery" ]; then
    echo "[entrypoint] applying migrations..."
    python manage.py migrate --noinput
    echo "[entrypoint] seeding plans..."
    python manage.py manage_plans || true
fi

echo "[entrypoint] starting: $*"
exec "$@"
