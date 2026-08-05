#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/verify_backup.sh backups/flashin_YYYYMMDD_HHMMSS.sql.gz" >&2
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

python3 "$INTEGRITY_SCRIPT" verify \
  --backup "$FILE" \
  --manifest "$MANIFEST_FILE" \
  --env "$INTEGRITY_ENV"

echo "Backup signature, archive, schema and critical data verification OK"
