#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Before the container starts:
#   1. Wait for Postgres to be ready (do not rely on the compose healthcheck)
#   2. Apply migrations
#   3. Collect static files (collect again in case of a volume mount)
#   4. Then run CMD (gunicorn / celery worker)
#
# Option to skip migrations (e.g. for the celery worker): SKIP_MIGRATIONS=1
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
    print("[entrypoint] sqlite - no wait needed")
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
raise SystemExit("[entrypoint] database not ready after 60s - exiting")
PY

if [ "${SKIP_MIGRATIONS:-0}" != "1" ] && [ "${1:-}" != "celery" ]; then
    echo "[entrypoint] applying migrations..."
    python manage.py migrate --noinput
    echo "[entrypoint] seeding plans..."
    python manage.py manage_plans || true
fi

echo "[entrypoint] starting: $*"
exec "$@"
