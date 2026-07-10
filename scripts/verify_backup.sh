#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: scripts/verify_backup.sh backups/flashin_YYYYMMDD_HHMMSS.sql.gz"
  exit 1
fi

FILE=$1
TEST_DB=${TEST_DB:-flashin_restore_check}
CONTAINER=${POSTGRES_CONTAINER:-flashin-miniapp-v42-db-1}
USER=${POSTGRES_USER:-flashin}

echo "Verifying backup $FILE"

docker exec "$CONTAINER" psql -U "$USER" -d postgres -c "DROP DATABASE IF EXISTS $TEST_DB;"
docker exec "$CONTAINER" psql -U "$USER" -d postgres -c "CREATE DATABASE $TEST_DB;"
gunzip -c "$FILE" | docker exec -i "$CONTAINER" psql -U "$USER" "$TEST_DB"
docker exec "$CONTAINER" psql -U "$USER" -d "$TEST_DB" -c "\dt"
docker exec "$CONTAINER" psql -U "$USER" -d postgres -c "DROP DATABASE $TEST_DB;"

echo "Backup verification OK"
