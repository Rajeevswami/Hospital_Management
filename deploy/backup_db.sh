#!/usr/bin/env bash
###############################################################################
# PostgreSQL backup + restore helper (Phase 0 - safety net)
#
# What changed (vs the old script):
#   * DB credentials are no longer HARDCODED - they come from .env / the
#     environment (DATABASE_URL, or PG* vars). On Render, DATABASE_URL is
#     already set.
#   * Backups use the custom format (-Fc) -> selective/parallel restore is
#     possible, and the dump's integrity is verified.
#   * A `restore` mode was added, so a backup taken before a migration can be
#     reapplied with a single command.
#
# USAGE
#   ./deploy/backup_db.sh                  # take a backup (auto-delete after 14 days)
#   ./deploy/backup_db.sh --tag pre-saas   # tagged backup (safe from auto-delete)
#   BACKUP_DIR=/mnt/usb ./deploy/backup_db.sh
#   ./deploy/backup_db.sh restore <file>   # restore (asks for confirmation)
#   ./deploy/backup_db.sh list             # show available backups
#
# CRON (VPS, nightly at 2 AM, 14-day retention):
#   0 2 * * * cd /var/www/hospital_system && ./deploy/backup_db.sh >> /var/log/hospital_system/backup.log 2>&1
#
# Manual backup ON RENDER:
#   Render Dashboard > PostgreSQL > Manual Backup (snapshot), or
#   run this same script inside render shell / ssh with DATABASE_URL set.
###############################################################################
set -euo pipefail

MODE="backup"
TAG=""
RETENTION_DAYS="${RETENTION_DAYS:-14}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/hospital_system}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

# ---------------- args ----------------
while [ $# -gt 0 ]; do
  case "$1" in
    restore) MODE="restore"; shift; DUMP_FILE="${1:-}"; shift || true ;;
    list)    MODE="list"; shift ;;
    --tag)   TAG="$2"; shift 2 ;;
    --dir)   BACKUP_DIR="$2"; shift 2 ;;
    --keep-days) RETENTION_DAYS="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# ---------------- .env load (if DATABASE_URL is not already in the env) ----------------
if [ -z "${DATABASE_URL:-}" ] && [ -f .env ]; then
  # pick up only the DATABASE_URL line; do not export the other secrets
  DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | tail -n1 | cut -d= -f2- | tr -d '"' || true)"
  export DATABASE_URL
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL is not set (in env or .env)." >&2
  echo "       e.g. export DATABASE_URL='postgres://USER:<PASSWORD>@HOST:5432/DBNAME'" >&2
  exit 1
fi

for bin in pg_dump psql; do
  command -v "$bin" >/dev/null 2>&1 || { echo "ERROR: '$bin' is not installed." >&2; exit 1; }
done

run_sql() { psql "$DATABASE_URL" -tAc "$1"; }

# ---------------- LIST ----------------
if [ "$MODE" = "list" ]; then
  echo "DB: $(echo "$DATABASE_URL" | sed -E 's#://([^:]+):[^@]+@#://\1:***@#')"
  ls -lh "$BACKUP_DIR" 2>/dev/null || echo "(no backups in $BACKUP_DIR)"
  exit 0
fi

# ---------------- RESTORE ----------------
if [ "$MODE" = "restore" ]; then
  [ -n "${DUMP_FILE:-}" ] || { echo "Usage: $0 restore <dump-file>" >&2; exit 2; }
  [ -f "$DUMP_FILE" ] || { echo "ERROR: file not found: $DUMP_FILE" >&2; exit 1; }
  echo "WARNING: the database $(echo "$DATABASE_URL" | sed -E 's#://([^:]+):[^@]+@#://\1:***@#') will be OVERWRITTEN from '$DUMP_FILE'."
  printf "Type 'RESTORE' to continue: "
  read -r CONFIRM
  [ "$CONFIRM" = "RESTORE" ] || { echo "Cancelled."; exit 1; }
  pg_restore --no-owner --clean --if-exists --dbname="$DATABASE_URL" "$DUMP_FILE"
  echo "Restore complete."
  exit 0
fi

# ---------------- BACKUP ----------------
mkdir -p "$BACKUP_DIR"

DB_LABEL="$(run_sql 'SELECT current_database()')"
DB_SIZE="$(run_sql "SELECT pg_size_pretty(pg_database_size(current_database()))")"
echo "[$(date)] Backing up '$DB_LABEL' (size: $DB_SIZE)"

if [ -n "$TAG" ]; then
  OUT="$BACKUP_DIR/hospital_db_${TAG}_${TIMESTAMP}.dump"
else
  OUT="$BACKUP_DIR/hospital_db_${TIMESTAMP}.dump"
fi

# -Fc = custom format (compressed, verifiable, selective restore)
pg_dump --dbname="$DATABASE_URL" --format=custom --no-owner --file="$OUT"

# Integrity check: is the dump readable? (a broken backup is the worst backup)
TABLE_COUNT="$(pg_restore --list "$OUT" | grep -c 'TABLE DATA' || true)"
FILE_SIZE="$(du -h "$OUT" | cut -f1)"
echo "[$(date)] OK -> $OUT ($FILE_SIZE, $TABLE_COUNT tables with data)"

# ---------------- RETENTION (tagged backups are never auto-deleted) ----------------
if [ -z "$TAG" ]; then
  find "$BACKUP_DIR" -maxdepth 1 -name "hospital_db_2*.dump" -mtime +"$RETENTION_DAYS" -print -delete \
    | sed 's/^/  pruned: /' || true
  find "$BACKUP_DIR" -maxdepth 1 -name "hospital_db_2*.sql.gz" -mtime +"$RETENTION_DAYS" -print -delete \
    | sed 's/^/  pruned (legacy): /' || true
else
  echo "  tagged backup '$TAG' is exempt from retention (delete it manually)."
fi

echo "[$(date)] Backup completed."
