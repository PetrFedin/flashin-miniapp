#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/verify_backup.sh backups/flashin_YYYYMMDD_HHMMSS.sql.gz"
  exit 1
fi

FILE=$1
TEST_DB=${TEST_DB:-flashin_restore_check}

if [ ! -f "$FILE" ]; then
  echo "Backup file not found: $FILE"
  exit 1
fi
if ! gzip -t "$FILE"; then
  echo "Backup archive is invalid: $FILE"
  exit 1
fi
if ! [[ "$TEST_DB" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "TEST_DB must be a valid PostgreSQL identifier"
  exit 1
fi
if ! docker compose ps --status running --services | grep -qx db; then
  echo "PostgreSQL Compose service is not running"
  exit 1
fi

echo "Verifying backup $FILE"

cleanup() {
  docker compose exec -T db sh -ec \
    'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d postgres -v test_db="$1" -c "DROP DATABASE IF EXISTS :\"test_db\";"' \
    sh "$TEST_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose exec -T db sh -ec \
  'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d postgres -v test_db="$1" -c "DROP DATABASE IF EXISTS :\"test_db\";" -c "CREATE DATABASE :\"test_db\";"' \
  sh "$TEST_DB"

gunzip -c "$FILE" \
  | docker compose exec -T db sh -ec \
      'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$1"' sh "$TEST_DB"

docker compose exec -T db sh -ec \
  'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$1" -tAc "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = '\''public'\'';"' \
  sh "$TEST_DB" \
  | awk '{ if ($1 <= 0) exit 1; print "Restored public tables:", $1 }'

cleanup
trap - EXIT

echo "Backup verification OK"
