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

mkdir -p "$(dirname "$BACKUP_FILE")"

if ! docker compose ps --status running --services | grep -qx db; then
  echo "PostgreSQL Compose service is not running" >&2
  exit 1
fi

tmp_file="${BACKUP_FILE}.tmp"
trap 'rm -f "$tmp_file"' EXIT

docker compose exec -T db sh -ec 'pg_dump --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip -9 > "$tmp_file"

test -s "$tmp_file"
gzip -t "$tmp_file"
mv "$tmp_file" "$BACKUP_FILE"
trap - EXIT

if [ "$PRINT_PATH_ONLY" -eq 1 ]; then
  printf '%s\n' "$BACKUP_FILE"
else
  echo "Backup created and gzip-verified: $BACKUP_FILE"
fi
