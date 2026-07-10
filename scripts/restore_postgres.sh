#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: scripts/restore_postgres.sh backups/flashin_YYYYMMDD_HHMMSS.sql.gz"
  exit 1
fi

FILE=$1
CONTAINER=${POSTGRES_CONTAINER:-flashin-miniapp-v30-db-1}
DB=${POSTGRES_DB:-flashin}
USER=${POSTGRES_USER:-flashin}

gunzip -c "$FILE" | docker exec -i "$CONTAINER" psql -U "$USER" "$DB"

echo "Restore completed from: $FILE"
