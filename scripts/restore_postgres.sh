#!/usr/bin/env bash
set -euo pipefail

ASSUME_YES=0
if [ "${1:-}" = "--yes" ]; then
  ASSUME_YES=1
  shift
fi
if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/restore_postgres.sh [--yes] backups/flashin_YYYYMMDD_HHMMSS.sql.gz" >&2
  exit 1
fi

FILE=$1
MANIFEST_FILE=${BACKUP_MANIFEST_FILE:-"${FILE}.manifest.json"}
INTEGRITY_SCRIPT=${BACKUP_INTEGRITY_SCRIPT:-scripts/backup_integrity.py}
INTEGRITY_ENV=${BACKUP_INTEGRITY_ENV:-.env}

if [ ! -f "$FILE" ]; then
  echo "Backup file not found: $FILE" >&2
  exit 1
fi
if [ ! -f "$MANIFEST_FILE" ]; then
  echo "Signed backup manifest not found: $MANIFEST_FILE" >&2
  exit 1
fi
if [ ! -f "$INTEGRITY_SCRIPT" ]; then
  echo "Backup integrity script is missing: $INTEGRITY_SCRIPT" >&2
  exit 1
fi
if ! gzip -t "$FILE"; then
  echo "Backup archive is invalid: $FILE" >&2
  exit 1
fi
if ! docker compose ps --status running --services | grep -qx db; then
  echo "PostgreSQL Compose service is not running" >&2
  exit 1
fi

if [ "${ALLOW_LIVE_RESTORE:-0}" != "1" ]; then
  for service in backend bot notification_worker scheduler; do
    if docker compose ps --status running --services | grep -qx "$service"; then
      echo "Refusing restore while $service is running. Stop application services first." >&2
      exit 1
    fi
  done
fi

DB_NAME=$(docker compose exec -T db sh -ec 'printf %s "$POSTGRES_DB"')
DB_USER=$(docker compose exec -T db sh -ec 'printf %s "$POSTGRES_USER"')
if ! [[ "$DB_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "POSTGRES_DB must be a valid PostgreSQL identifier" >&2
  exit 1
fi
if ! [[ "$DB_USER" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "POSTGRES_USER must be a valid PostgreSQL identifier" >&2
  exit 1
fi
case "$DB_NAME" in
  postgres|template0|template1)
    echo "Refusing to overwrite reserved PostgreSQL database: $DB_NAME" >&2
    exit 1
    ;;
esac

# Prove the exact signed archive is restorable before destroying the live database.
python3 "$INTEGRITY_SCRIPT" verify \
  --backup "$FILE" \
  --manifest "$MANIFEST_FILE" \
  --env "$INTEGRITY_ENV" >/dev/null

if [ "$ASSUME_YES" -ne 1 ]; then
  read -r -p "Restore will drop and recreate database '$DB_NAME'. Type RESTORE to continue: " confirmation
  if [ "$confirmation" != "RESTORE" ]; then
    echo "Restore cancelled"
    exit 1
  fi
fi

echo "Dropping and recreating database $DB_NAME before restore..."
docker compose exec -T db sh -ec '
  database=$1
  owner=$2
  psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '\''${database}'\'' AND pid <> pg_backend_pid();" \
    -c "DROP DATABASE IF EXISTS \"${database}\";" \
    -c "CREATE DATABASE \"${database}\" OWNER \"${owner}\";"
' sh "$DB_NAME" "$DB_USER"

gunzip -c "$FILE" \
  | docker compose exec -T db sh -ec 'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" "$POSTGRES_DB"'

TABLE_COUNT=$(docker compose exec -T db sh -ec \
  'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = '\''public'\'';"' \
  | tr -d '[:space:]')
if [ -z "$TABLE_COUNT" ] || [ "$TABLE_COUNT" -le 0 ]; then
  echo "Restore validation failed: no public tables found" >&2
  exit 1
fi

ALEMBIC_TABLE=$(docker compose exec -T db sh -ec \
  'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT to_regclass('\''public.alembic_version'\'') IS NOT NULL;"' \
  | tr -d '[:space:]')
if [ "$ALEMBIC_TABLE" != "t" ]; then
  echo "Restore validation failed: alembic_version table is missing" >&2
  exit 1
fi

# Compare the restored target against the signed archive snapshot, including the
# Alembic revision, complete public schema and critical business-ledger content.
python3 "$INTEGRITY_SCRIPT" verify-live \
  --backup "$FILE" \
  --manifest "$MANIFEST_FILE" \
  --env "$INTEGRITY_ENV" \
  --database "$DB_NAME" >/dev/null

echo "Restore completed and signed snapshot verified from: $FILE ($TABLE_COUNT public tables)"
