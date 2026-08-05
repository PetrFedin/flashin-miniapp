#!/usr/bin/env bash
set -euo pipefail

if ! docker compose ps --status running --services | grep -qx db; then
  echo "PostgreSQL Compose service is not running" >&2
  exit 1
fi

INTEGRITY_SCRIPT=${BACKUP_INTEGRITY_SCRIPT:-scripts/backup_integrity.py}
INTEGRITY_ENV=${BACKUP_INTEGRITY_ENV:-.env}
TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(10))')
TELEGRAM_ID="backup_${TOKEN}"
ORIGINAL_NAME="Backup Sentinel ${TOKEN}"
MUTATED_NAME="Mutated Sentinel ${TOKEN}"
DB_NAME=$(docker compose exec -T db sh -ec 'printf %s "$POSTGRES_DB"')

case "$TELEGRAM_ID" in
  *[!A-Za-z0-9_]*) echo "Generated sentinel identity is invalid" >&2; exit 1 ;;
esac
case "$DB_NAME" in
  ''|*[!A-Za-z0-9_]*) echo "POSTGRES_DB is invalid" >&2; exit 1 ;;
esac

TMP_DIR=$(mktemp -d)
BACKUP_FILE="$TMP_DIR/flashin_restore_smoke.sql.gz"
MANIFEST_FILE="${BACKUP_FILE}.manifest.json"
TAMPERED_FILE="$TMP_DIR/flashin_restore_smoke_tampered.sql.gz"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

insert_sql="INSERT INTO public.customers (telegram_id, username, first_name, last_name, phone, email, created_at) VALUES ('$TELEGRAM_ID', '', '$ORIGINAL_NAME', '', '', '', NOW());"
docker compose exec -T db sh -ec \
  'exec psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' \
  sh "$insert_sql" >/dev/null

BACKUP_FILE="$BACKUP_FILE" \
BACKUP_MANIFEST_FILE="$MANIFEST_FILE" \
BACKUP_INTEGRITY_ENV="$INTEGRITY_ENV" \
  bash scripts/backup_postgres.sh >/dev/null

test -s "$BACKUP_FILE"
test -s "$MANIFEST_FILE"

BACKUP_MANIFEST_FILE="$MANIFEST_FILE" \
BACKUP_INTEGRITY_ENV="$INTEGRITY_ENV" \
  bash scripts/verify_backup.sh "$BACKUP_FILE" >/dev/null

cp "$BACKUP_FILE" "$TAMPERED_FILE"
printf 'tampered' >> "$TAMPERED_FILE"
if python3 "$INTEGRITY_SCRIPT" verify-archive \
  --backup "$TAMPERED_FILE" \
  --manifest "$MANIFEST_FILE" \
  --env "$INTEGRITY_ENV" >/dev/null 2>&1; then
  echo "Tampered backup unexpectedly passed signed archive verification" >&2
  exit 1
fi

mutate_sql="UPDATE public.customers SET first_name = '$MUTATED_NAME' WHERE telegram_id = '$TELEGRAM_ID';"
docker compose exec -T db sh -ec \
  'exec psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' \
  sh "$mutate_sql" >/dev/null

if python3 "$INTEGRITY_SCRIPT" verify-live \
  --backup "$BACKUP_FILE" \
  --manifest "$MANIFEST_FILE" \
  --env "$INTEGRITY_ENV" \
  --database "$DB_NAME" >/dev/null 2>&1; then
  echo "Mutated live database unexpectedly matched the backup manifest" >&2
  exit 1
fi

BACKUP_MANIFEST_FILE="$MANIFEST_FILE" \
BACKUP_INTEGRITY_ENV="$INTEGRITY_ENV" \
  bash scripts/restore_postgres.sh --yes "$BACKUP_FILE" >/dev/null

restored_name=$(docker compose exec -T db sh -ec \
  'exec psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAX -c "$1"' \
  sh "SELECT first_name FROM public.customers WHERE telegram_id = '$TELEGRAM_ID';")
if [ "$restored_name" != "$ORIGINAL_NAME" ]; then
  echo "Restored sentinel mismatch: expected '$ORIGINAL_NAME', got '$restored_name'" >&2
  exit 1
fi

python3 "$INTEGRITY_SCRIPT" verify-live \
  --backup "$BACKUP_FILE" \
  --manifest "$MANIFEST_FILE" \
  --env "$INTEGRITY_ENV" \
  --database "$DB_NAME" >/dev/null

python3 - <<PY
import json
print(json.dumps({
    "status": "ok",
    "database": "$DB_NAME",
    "sentinel": "$TELEGRAM_ID",
    "tampered_archive_rejected": True,
    "mutated_database_rejected": True,
    "restored_value_verified": True,
    "signed_manifest_verified": True,
}, ensure_ascii=False))
PY
