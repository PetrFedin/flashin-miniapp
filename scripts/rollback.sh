#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: scripts/rollback.sh /path/to/previous-release.zip [backup.sql.gz]"
  exit 1
fi

RELEASE_ZIP=$1
BACKUP=${2:-}

echo "Rolling back to $RELEASE_ZIP"

docker compose down

TMP_DIR=$(mktemp -d)
unzip -q "$RELEASE_ZIP" -d "$TMP_DIR"

rsync -a --delete "$TMP_DIR"/ ./

if [ -n "$BACKUP" ]; then
  echo "Restoring database from $BACKUP"
  scripts/restore_postgres.sh "$BACKUP"
fi

docker compose up -d --build backend frontend admin bot
python3 tests/e2e_smoke.py

echo "Rollback completed"
