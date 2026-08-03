#!/usr/bin/env bash
set -euo pipefail

RELEASE_REF=${1:-previous}
BACKUP=${2:-}

export COMPOSE_FILE=${COMPOSE_FILE:-"docker-compose.yml:docker-compose.production.yml"}
export COMPOSE_PROFILES=${COMPOSE_PROFILES:-"production,workers,scheduler,search"}

CONTROL_SCRIPT="scripts/release_control.py"
RESTORE_SCRIPT="scripts/restore_postgres.sh"
if [ ! -f "$CONTROL_SCRIPT" ]; then
  echo "Release control script is missing: $CONTROL_SCRIPT" >&2
  exit 1
fi
if [ ! -f "$RESTORE_SCRIPT" ]; then
  echo "Safe restore script is missing: $RESTORE_SCRIPT" >&2
  exit 1
fi

if [ "$RELEASE_REF" = "current" ] || [ "$RELEASE_REF" = "previous" ]; then
  RELEASE=$(python3 "$CONTROL_SCRIPT" resolve --slot "$RELEASE_REF")
else
  RELEASE=$RELEASE_REF
fi
RELEASE=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$RELEASE")

if [ -n "$BACKUP" ]; then
  BACKUP=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$BACKUP")
else
  if [ "${ALLOW_CODE_ONLY_ROLLBACK:-0}" != "1" ]; then
    echo "Database backup is required. Set ALLOW_CODE_ONLY_ROLLBACK=1 only after confirming schema compatibility." >&2
    exit 1
  fi
fi

python3 "$CONTROL_SCRIPT" verify --archive "$RELEASE" >/dev/null
if [ -n "$BACKUP" ]; then
  scripts/verify_backup.sh "$BACKUP"
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required for rollback" >&2
  exit 1
fi

echo "Rolling back to verified release: $RELEASE"
[ -z "$BACKUP" ] || echo "Database restore source: $BACKUP"

docker compose down

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
cp "$CONTROL_SCRIPT" "$TMP_DIR/release_control.py"
cp "$RESTORE_SCRIPT" "$TMP_DIR/restore_postgres.sh"
chmod +x "$TMP_DIR/restore_postgres.sh"
python3 "$TMP_DIR/release_control.py" extract --archive "$RELEASE" --destination "$TMP_DIR/release" >/dev/null
rm -f "$TMP_DIR/release/release_manifest.json"

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'backups/' \
  --exclude 'media/' \
  --exclude 'exports/' \
  --exclude 'logs/' \
  --exclude 'postgres-data/' \
  --exclude 'meili_data/' \
  --exclude 'grafana-data/' \
  --exclude 'deploy/release/builds/' \
  --exclude 'deploy/release/runtime/' \
  --exclude 'docs/pilot/live_pilot_state.json' \
  --exclude 'docs/pilot/live_pilot_summary.md' \
  --exclude 'docs/pilot/integration_check_report.json' \
  --exclude 'docs/pilot/integration_check_report.md' \
  --exclude 'docs/readiness_gate_report.json' \
  --exclude 'docs/readiness_gate_report.md' \
  --exclude 'docs/pilot_live_gate_report.json' \
  --exclude 'docs/pilot_live_gate_report.md' \
  "$TMP_DIR/release/" ./

echo "Starting PostgreSQL for rollback validation..."
docker compose up -d db
for _ in $(seq 1 60); do
  if docker compose exec -T db sh -ec 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! docker compose exec -T db sh -ec 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
  echo "PostgreSQL did not become ready during rollback" >&2
  docker compose logs db
  exit 1
fi

if [ -n "$BACKUP" ]; then
  "$TMP_DIR/restore_postgres.sh" --yes "$BACKUP"
else
  echo "Code-only rollback explicitly authorized; database was not modified."
fi

echo "Checking rollback migration compatibility..."
docker compose run --rm backend alembic -c backend/alembic.ini current
docker compose run --rm backend python scripts/check_transaction_integrity.py

echo "Starting rolled-back production services..."
docker compose up -d db backend frontend admin bot caddy notification_worker scheduler meilisearch

backend_ready=0
for _ in $(seq 1 90); do
  if docker compose exec -T backend curl -fsS http://localhost:8000/ready >/dev/null 2>&1; then
    backend_ready=1
    break
  fi
  sleep 2
done
if [ "$backend_ready" -ne 1 ]; then
  echo "Rolled-back backend did not become ready" >&2
  docker compose logs backend
  exit 1
fi

for _ in $(seq 1 60); do
  if docker compose exec -T backend curl -fsS http://meilisearch:7700/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! docker compose exec -T backend curl -fsS http://meilisearch:7700/health >/dev/null 2>&1; then
  echo "Meilisearch did not become healthy after rollback" >&2
  docker compose logs meilisearch
  exit 1
fi

docker compose run --rm backend python scripts/configure_meilisearch.py
for service in db backend frontend admin bot caddy notification_worker scheduler meilisearch; do
  if ! docker compose ps --status running --services | grep -qx "$service"; then
    echo "$service is not running after rollback" >&2
    docker compose logs "$service"
    exit 1
  fi
done

docker compose exec -T backend python scripts/container_smoke.py
python3 "$TMP_DIR/release_control.py" promote --archive "$RELEASE" >/dev/null

echo "Rollback completed and release pointer promoted: $RELEASE"
