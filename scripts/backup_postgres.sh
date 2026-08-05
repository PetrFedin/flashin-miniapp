#!/usr/bin/env bash
set -euo pipefail

PRINT_PATH_ONLY=0
if [ "${1:-}" = "--print-path" ]; then
  PRINT_PATH_ONLY=1
  shift
fi
if [ "$#" -ne 0 ]; then
  echo "Usage: scripts/backup_postgres.sh [--print-path]" >&2
  exit 1
fi

BACKUP_DIR=${BACKUP_DIR:-./backups}
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE=${BACKUP_FILE:-"$BACKUP_DIR/flashin_${TIMESTAMP}.sql.gz"}
MANIFEST_FILE=${BACKUP_MANIFEST_FILE:-"${BACKUP_FILE}.manifest.json"}
INTEGRITY_SCRIPT=${BACKUP_INTEGRITY_SCRIPT:-scripts/backup_integrity.py}
INTEGRITY_ENV=${BACKUP_INTEGRITY_ENV:-.env}

if [ ! -f "$INTEGRITY_SCRIPT" ]; then
  echo "Backup integrity script is missing: $INTEGRITY_SCRIPT" >&2
  exit 1
fi
mkdir -p "$(dirname "$BACKUP_FILE")" "$(dirname "$MANIFEST_FILE")"

if ! docker compose ps --status running --services | grep -qx db; then
  echo "PostgreSQL Compose service is not running" >&2
  exit 1
fi

tmp_file="${BACKUP_FILE}.tmp"
trap 'rm -f "$tmp_file" "$BACKUP_FILE" "$MANIFEST_FILE"' EXIT

docker compose exec -T db sh -ec 'pg_dump --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip -9 > "$tmp_file"

test -s "$tmp_file"
gzip -t "$tmp_file"
mv "$tmp_file" "$BACKUP_FILE"

python3 "$INTEGRITY_SCRIPT" create \
  --backup "$BACKUP_FILE" \
  --manifest "$MANIFEST_FILE" \
  --env "$INTEGRITY_ENV" >/dev/null

test -s "$MANIFEST_FILE"
trap - EXIT

if [ "$PRINT_PATH_ONLY" -eq 1 ]; then
  printf '%s\n' "$BACKUP_FILE"
else
  echo "Backup created, restored in isolation and signed: $BACKUP_FILE"
  echo "Backup manifest: $MANIFEST_FILE"
fi
