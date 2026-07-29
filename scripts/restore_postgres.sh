#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/restore_postgres.sh backups/flashin_YYYYMMDD_HHMMSS.sql.gz"
  exit 1
fi

FILE=$1
if [ ! -f "$FILE" ]; then
  echo "Backup file not found: $FILE"
  exit 1
fi
if ! gzip -t "$FILE"; then
  echo "Backup archive is invalid: $FILE"
  exit 1
fi
if ! docker compose ps --status running --services | grep -qx db; then
  echo "PostgreSQL Compose service is not running"
  exit 1
fi

read -r -p "Restore will overwrite data in the configured database. Type RESTORE to continue: " confirmation
if [ "$confirmation" != "RESTORE" ]; then
  echo "Restore cancelled"
  exit 1
fi

gunzip -c "$FILE" \
  | docker compose exec -T db sh -ec 'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" "$POSTGRES_DB"'

echo "Restore completed from: $FILE"
