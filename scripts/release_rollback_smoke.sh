#!/usr/bin/env bash
set -euo pipefail

ROOT=$(pwd)
TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(6))')
TMP_DIR=$(mktemp -d)
RELEASE_REPO="$TMP_DIR/release-repo"
OVERRIDE_FILE="$TMP_DIR/docker-compose.rollback-smoke.yml"
BACKUP_FILE="$TMP_DIR/rollback-smoke.sql.gz"
BACKUP_MANIFEST_FILE="${BACKUP_FILE}.manifest.json"
MARKER_FILE="docs/pilot/rollback_version_marker.txt"
REPORT_JSON="docs/pilot/rollback_drill_report.json"
REPORT_MD="docs/pilot/rollback_drill_report.md"
TELEGRAM_ID="rollback_${TOKEN}"
ORIGINAL_NAME="Rollback Sentinel ${TOKEN}"
MUTATED_NAME="Mutated Rollback ${TOKEN}"
PREVIOUS_MARKER="previous-release-${TOKEN}"
CURRENT_MARKER="current-release-${TOKEN}"

cleanup() {
  docker compose down -v >/dev/null 2>&1 || true
  rm -f "$MARKER_FILE" "$REPORT_JSON" "$REPORT_MD"
  rm -rf deploy/release/runtime
  rm -f deploy/release/builds/flashin_rollback-previous-${TOKEN}.zip*
  rm -f deploy/release/builds/flashin_rollback-current-${TOKEN}.zip*
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

for command in git docker rsync python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required rollback smoke command is unavailable: $command" >&2
    exit 1
  fi
done

cat > "$OVERRIDE_FILE" <<'YAML'
services:
  bot:
    command: ["sh", "-ec", "trap : TERM INT; sleep infinity & wait"]
  notification_worker:
    command: ["sh", "-ec", "trap : TERM INT; sleep infinity & wait"]
  scheduler:
    command: ["sh", "-ec", "trap : TERM INT; sleep infinity & wait"]
YAML

export COMPOSE_FILE="$ROOT/docker-compose.yml:$OVERRIDE_FILE"
export COMPOSE_PROFILES="production,workers,scheduler,search"

rm -rf deploy/release/runtime
mkdir -p deploy/release/runtime deploy/release/builds docs/pilot

git clone --quiet --no-hardlinks "$ROOT" "$RELEASE_REPO"
git -C "$RELEASE_REPO" config user.email "rollback-smoke@flashin.local"
git -C "$RELEASE_REPO" config user.name "FLASHIN Rollback Smoke"

printf '%s\n' "$PREVIOUS_MARKER" > "$RELEASE_REPO/$MARKER_FILE"
git -C "$RELEASE_REPO" add "$MARKER_FILE"
git -C "$RELEASE_REPO" commit -qm "Add previous rollback marker"
PREVIOUS_ARCHIVE=$(python3 scripts/release_control.py create \
  --root "$RELEASE_REPO" \
  --output-dir "$ROOT/deploy/release/builds" \
  --release-id "rollback-previous-${TOKEN}" \
  --print-path)
python3 scripts/release_control.py verify --archive "$PREVIOUS_ARCHIVE" >/dev/null
python3 scripts/pilot_release_capability.py inspect --archive "$PREVIOUS_ARCHIVE" >/dev/null

printf '%s\n' "$CURRENT_MARKER" > "$RELEASE_REPO/$MARKER_FILE"
git -C "$RELEASE_REPO" add "$MARKER_FILE"
git -C "$RELEASE_REPO" commit -qm "Add current rollback marker"
CURRENT_ARCHIVE=$(python3 scripts/release_control.py create \
  --root "$RELEASE_REPO" \
  --output-dir "$ROOT/deploy/release/builds" \
  --release-id "rollback-current-${TOKEN}" \
  --print-path)
python3 scripts/release_control.py verify --archive "$CURRENT_ARCHIVE" >/dev/null
python3 scripts/pilot_release_capability.py inspect --archive "$CURRENT_ARCHIVE" >/dev/null

python3 scripts/release_control.py promote --archive "$PREVIOUS_ARCHIVE" >/dev/null
python3 scripts/pilot_release_capability.py stamp --slot current --env .env >/dev/null
python3 scripts/release_control.py promote --archive "$CURRENT_ARCHIVE" >/dev/null
python3 scripts/pilot_release_capability.py stamp --slot current --env .env >/dev/null
python3 scripts/pilot_release_capability.py verify --slot both --env .env >/dev/null

printf '%s\n' "$CURRENT_MARKER" > "$MARKER_FILE"

docker compose up -d db
for _ in $(seq 1 60); do
  if docker compose exec -T db pg_isready -U flashin -d flashin >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker compose exec -T db pg_isready -U flashin -d flashin

docker compose run --rm backend alembic -c backend/alembic.ini upgrade head

insert_sql="INSERT INTO public.customers (telegram_id, username, first_name, last_name, phone, email, created_at) VALUES ('$TELEGRAM_ID', '', '$ORIGINAL_NAME', '', '', '', NOW());"
docker compose exec -T db sh -ec \
  'exec psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' \
  sh "$insert_sql" >/dev/null

BACKUP_FILE="$BACKUP_FILE" \
BACKUP_MANIFEST_FILE="$BACKUP_MANIFEST_FILE" \
BACKUP_INTEGRITY_ENV=.env \
  bash scripts/backup_postgres.sh >/dev/null

mutate_sql="UPDATE public.customers SET first_name = '$MUTATED_NAME' WHERE telegram_id = '$TELEGRAM_ID';"
docker compose exec -T db sh -ec \
  'exec psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' \
  sh "$mutate_sql" >/dev/null

# Start the current deployment so rollback proves runtime stop and service restart.
docker compose up -d db backend frontend admin bot caddy notification_worker scheduler meilisearch
for _ in $(seq 1 90); do
  if docker compose exec -T backend curl -fsS http://localhost:8000/ready >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker compose exec -T backend curl -fsS http://localhost:8000/ready >/dev/null

ROLLBACK_DRILL=1 bash scripts/rollback.sh "$PREVIOUS_ARCHIVE" "$BACKUP_FILE"

restored_marker=$(tr -d '\r\n' < "$MARKER_FILE")
if [ "$restored_marker" != "$PREVIOUS_MARKER" ]; then
  echo "Release marker was not rolled back: expected '$PREVIOUS_MARKER', got '$restored_marker'" >&2
  exit 1
fi

restored_name=$(docker compose exec -T db sh -ec \
  'exec psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAX -c "$1"' \
  sh "SELECT first_name FROM public.customers WHERE telegram_id = '$TELEGRAM_ID';")
if [ "$restored_name" != "$ORIGINAL_NAME" ]; then
  echo "Rollback database sentinel mismatch: expected '$ORIGINAL_NAME', got '$restored_name'" >&2
  exit 1
fi

python3 scripts/backup_integrity.py verify-live \
  --backup "$BACKUP_FILE" \
  --manifest "$BACKUP_MANIFEST_FILE" \
  --env .env \
  --database flashin >/dev/null
python3 scripts/pilot_release_capability.py verify --slot both --env .env >/dev/null
python3 scripts/pilot_evidence.py verify-rollback --env .env --report "$REPORT_JSON" >/dev/null

python3 - "$PREVIOUS_ARCHIVE" "$CURRENT_ARCHIVE" "$REPORT_JSON" <<'PY'
import json
import sys
from pathlib import Path

previous_archive, current_archive, report_path = map(Path, sys.argv[1:])
current_state = json.loads(Path("deploy/release/runtime/current_release.json").read_text(encoding="utf-8"))
previous_state = json.loads(Path("deploy/release/runtime/previous_release.json").read_text(encoding="utf-8"))
report = json.loads(report_path.read_text(encoding="utf-8"))

assert Path(current_state["archive"]).resolve() == previous_archive.resolve()
assert Path(previous_state["archive"]).resolve() == current_archive.resolve()
assert report["result"] == "GO"
assert report["from_release"]["sha256"] != report["to_release"]["sha256"]
assert all(report["checks"].values())
print(json.dumps({
    "status": "ok",
    "from_release": report["from_release"]["release_id"],
    "to_release": report["to_release"]["release_id"],
    "database_restored": report["checks"]["database_restored"],
    "services_running": report["checks"]["services_running"],
    "container_smoke": report["checks"]["container_smoke"],
    "release_pointer_promoted": True,
    "signed_evidence_verified": True,
}, ensure_ascii=False))
PY

docker compose down -v
