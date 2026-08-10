#!/usr/bin/env bash
set -euo pipefail

echo "FLASHIN production deploy"

if [ ! -f .env ]; then
  echo ".env is missing. Copy .env.production.example to .env and fill secrets."
  exit 1
fi

release_input="${RELEASE:-${1:-}}"
if [ -z "$release_input" ]; then
  echo "Usage: RELEASE=deploy/release/builds/flashin_<release>.zip make deploy-prod" >&2
  echo "The archive and adjacent .sha256 must come from the retained exact-green Release artifact." >&2
  exit 2
fi

export COMPOSE_FILE="docker-compose.yml:docker-compose.production.yml"
export COMPOSE_PROFILES="production,workers,scheduler,search,monitoring"

release_source_dir=""
cleanup_release_source() {
  if [ -n "$release_source_dir" ] && [ -d "$release_source_dir" ]; then
    rm -rf "$release_source_dir"
  fi
}
trap cleanup_release_source EXIT

echo "Verifying retained immutable Release artifact before any runtime mutation..."
release_archive="$(python3 scripts/deploy_release_gate.py --archive "$release_input" --print-path)"
python3 scripts/pilot_release_capability.py inspect --archive "$release_archive" >/dev/null

echo "Materializing verified Release artifact as the only Docker build context..."
release_source_dir="$(mktemp -d "${TMPDIR:-/tmp}/flashin-release.XXXXXX")"
python3 scripts/release_control.py extract \
  --archive "$release_archive" \
  --destination "$release_source_dir" >/dev/null
cp .env "$release_source_dir/.env"
chmod 600 "$release_source_dir/.env"
compose_project="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}" 

if docker compose ps --status running --services 2>/dev/null | grep -qx backend; then
  if docker compose exec -T backend test -f /app/scripts/pilot_runtime.py; then
    echo "Stopping pilot checkout runtime before production deployment..."
    docker compose exec -T backend python scripts/pilot_runtime.py _stop \
      --reason "production deployment started"
  fi
fi

backup_file=""
deploy_failure() {
  status=$?
  echo "Production deploy failed with status $status." >&2
  if [ -n "$backup_file" ]; then
    echo "Verified pre-migration backup: $backup_file" >&2
    echo "Recovery command: scripts/rollback.sh previous '$backup_file'" >&2
  else
    echo "No database backup was created; failure occurred before an existing schema was migrated." >&2
  fi
  exit "$status"
}
trap deploy_failure ERR

echo "Running strict predeploy readiness gate..."
python3 scripts/readiness_gate.py --phase predeploy

echo "Rendering root-only Alertmanager configuration..."
python3 scripts/render_alertmanager_config.py

echo "Building images from verified immutable Release artifact..."
(
  unset COMPOSE_FILE
  cd "$release_source_dir"
  COMPOSE_PROJECT_NAME="$compose_project" \
    COMPOSE_PROFILES="$COMPOSE_PROFILES" \
    docker compose \
      -f docker-compose.yml \
      -f docker-compose.production.yml \
      build
)

# The host checkout remains the control plane for runtime Compose/ops commands.
# Reverify it after the build so drift cannot silently change those controls.
python3 scripts/deploy_release_gate.py --archive "$release_archive" >/dev/null
rm -rf "$release_source_dir"
release_source_dir=""

echo "Checking Alembic migration graph..."
alembic_heads="$(docker compose run --rm backend alembic -c backend/alembic.ini heads)"
printf '%s\n' "$alembic_heads"
if [ "$(printf '%s\n' "$alembic_heads" | grep -c '(head)')" -ne 1 ]; then
  echo "Alembic must have exactly one head"
  exit 1
fi

echo "Starting database..."
docker compose up -d db

echo "Waiting for PostgreSQL..."
db_ready=0
for _ in $(seq 1 60); do
  if docker compose exec -T db sh -ec 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    db_ready=1
    break
  fi
  sleep 2
done
if [ "$db_ready" -ne 1 ]; then
  echo "PostgreSQL did not become ready"
  docker compose logs db
  exit 1
fi

schema_exists="$(
  docker compose exec -T db sh -ec \
    'psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT to_regclass('\''public.alembic_version'\'') IS NOT NULL"' \
    | tr -d '[:space:]'
)"
if [ "$schema_exists" = "t" ]; then
  echo "Running pre-migration transaction integrity audit..."
  docker compose run --rm backend python scripts/check_transaction_integrity.py

  echo "Backing up database before migration..."
  backup_file=$(scripts/backup_postgres.sh --print-path)
  scripts/verify_backup.sh "$backup_file"
  echo "Verified pre-migration backup: $backup_file"
else
  echo "First deploy detected: no existing Alembic schema to back up or audit"
  docker compose run --rm backend python scripts/check_transaction_integrity.py --allow-missing-schema
fi

echo "Running migrations..."
docker compose run --rm backend alembic -c backend/alembic.ini upgrade head

echo "Verifying migration revision..."
docker compose run --rm backend alembic -c backend/alembic.ini current

echo "Verifying transaction integrity after migration..."
docker compose run --rm backend python scripts/check_transaction_integrity.py

echo "Verifying first-20-order runtime integrity..."
docker compose run --rm backend python scripts/check_pilot_runtime_integrity.py

if docker compose ps --status running --services 2>/dev/null | grep -qx prometheus; then
  echo "Stopping existing Prometheus before isolated Alertmanager delivery proof..."
  docker compose stop prometheus
fi

echo "Starting a fresh Alertmanager before the rest of monitoring..."
docker compose up -d --force-recreate alertmanager

echo "Proving isolated external alert delivery before release promotion..."
docker compose run --rm --no-deps backend python scripts/alertmanager_delivery_smoke.py

echo "Starting production services, durable provider worker, search and internal monitoring..."
docker compose up -d \
  db backend frontend admin bot caddy \
  notification_worker provider_command_jobs scheduler meilisearch \
  alertmanager prometheus grafana

echo "Waiting for migration-aware backend readiness inside Docker network..."
backend_ready=0
for _ in $(seq 1 90); do
  if docker compose exec -T backend curl -fsS http://localhost:8000/ready >/dev/null 2>&1; then
    backend_ready=1
    echo "Backend ready"
    break
  fi
  sleep 2
done
if [ "$backend_ready" -ne 1 ]; then
  echo "Backend did not become ready"
  docker compose logs backend
  exit 1
fi

echo "Waiting for Meilisearch inside Docker network..."
search_healthy=0
for _ in $(seq 1 60); do
  if docker compose exec -T backend curl -fsS http://meilisearch:7700/health >/dev/null 2>&1; then
    search_healthy=1
    echo "Meilisearch healthy"
    break
  fi
  sleep 2
done
if [ "$search_healthy" -ne 1 ]; then
  echo "Meilisearch did not become healthy"
  docker compose logs meilisearch
  exit 1
fi

echo "Applying search index settings..."
docker compose run --rm backend python scripts/configure_meilisearch.py

echo "Waiting for Prometheus rules, pilot metrics and Alertmanager discovery..."
prometheus_ready=0
for _ in $(seq 1 60); do
  metrics_payload="$(
    docker compose exec -T backend \
      curl -fsS 'http://prometheus:9090/api/v1/query?query=flashin_pilot_metrics_collection_success' \
      2>/dev/null || true
  )"
  rules_payload="$(
    docker compose exec -T backend \
      curl -fsS 'http://prometheus:9090/api/v1/rules' \
      2>/dev/null || true
  )"
  alertmanagers_payload="$(
    docker compose exec -T backend \
      curl -fsS 'http://prometheus:9090/api/v1/alertmanagers' \
      2>/dev/null || true
  )"
  if printf '%s' "$metrics_payload" | grep -q 'flashin_pilot_metrics_collection_success' \
    && printf '%s' "$rules_payload" | grep -q 'FlashinPilotRuntimeStopped' \
    && printf '%s' "$alertmanagers_payload" | grep -q 'alertmanager:9093'; then
    prometheus_ready=1
    echo "Prometheus pilot metrics, rules and Alertmanager discovery ready"
    break
  fi
  sleep 2
done
if [ "$prometheus_ready" -ne 1 ]; then
  echo "Prometheus did not load pilot metrics, alert rules or Alertmanager target"
  docker compose logs prometheus alertmanager
  exit 1
fi

echo "Waiting for Grafana health and provisioning..."
grafana_ready=0
for _ in $(seq 1 60); do
  if docker compose exec -T backend curl -fsS http://grafana:3000/api/health >/dev/null 2>&1; then
    grafana_ready=1
    echo "Grafana healthy"
    break
  fi
  sleep 2
done
if [ "$grafana_ready" -ne 1 ]; then
  echo "Grafana did not become healthy"
  docker compose logs grafana
  exit 1
fi

for service in \
  db backend frontend admin bot caddy \
  notification_worker provider_command_jobs scheduler meilisearch \
  alertmanager prometheus grafana; do
  if ! docker compose ps --status running --services | grep -qx "$service"; then
    echo "$service is not running"
    docker compose logs "$service"
    exit 1
  fi
done

echo "Running internal smoke checks..."
docker compose exec -T backend python scripts/container_smoke.py

echo "Promoting successful release pointer..."
python3 scripts/release_control.py promote --archive "$release_archive" >/dev/null
echo "Signing the promoted capability-v18 release pointer..."
python3 scripts/pilot_release_capability.py stamp --slot current --env .env >/dev/null
trap - ERR
echo "Deploy completed and release promoted: $release_archive"
if [ -n "$backup_file" ]; then
  echo "Rollback drill input: scripts/rollback.sh previous '$backup_file'"
fi
echo "Prometheus, Alertmanager and Grafana are internal-only. Use an authenticated SSH tunnel for operator access."
echo "Dedicated provider-command polling and scheduler fallback are both running."
echo "Isolated external alert delivery smoke passed before release promotion."
echo "Run 'make pilot-gate' only after the guarded current and previous releases, rollback drill and provider evidence are ready."
echo "Pilot runtime remains stopped. Re-run admission and 'make pilot-runtime-arm' before checkout."
