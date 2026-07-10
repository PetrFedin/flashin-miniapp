#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-./backups}
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "$BACKUP_DIR"

CONTAINER=${POSTGRES_CONTAINER:-flashin-miniapp-v30-db-1}
DB=${POSTGRES_DB:-flashin}
USER=${POSTGRES_USER:-flashin}

docker exec "$CONTAINER" pg_dump -U "$USER" "$DB" | gzip > "$BACKUP_DIR/flashin_${TIMESTAMP}.sql.gz"

echo "Backup created: $BACKUP_DIR/flashin_${TIMESTAMP}.sql.gz"
