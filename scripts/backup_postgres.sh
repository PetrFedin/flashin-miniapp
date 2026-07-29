#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-./backups}
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/flashin_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

if ! docker compose ps --status running --services | grep -qx db; then
  echo "PostgreSQL Compose service is not running"
  exit 1
fi

tmp_file="${BACKUP_FILE}.tmp"
trap 'rm -f "$tmp_file"' EXIT

docker compose exec -T db sh -ec 'pg_dump --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip -9 > "$tmp_file"

test -s "$tmp_file"
mv "$tmp_file" "$BACKUP_FILE"
trap - EXIT

echo "Backup created: $BACKUP_FILE"
