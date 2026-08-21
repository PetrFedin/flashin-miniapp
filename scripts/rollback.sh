#!/usr/bin/env bash
set -euo pipefail

RELEASE_REF=${1:-previous}
BACKUP=${2:-}
ROLLBACK_DRILL=${ROLLBACK_DRILL:-0}
RECOVERY_SCOPE=ci
RECOVERY_STARTED_AT=""

export COMPOSE_FILE=${COMPOSE_FILE:-"docker-compose.yml:docker-compose.production.yml"}
export COMPOSE_PROFILES=${COMPOSE_PROFILES:-"production,workers,scheduler,search,monitoring"}

PROJECT_ROOT=$(pwd)
RELEASE_STATE_DIR="$PROJECT_ROOT/deploy/release/runtime"
CONTROL_SCRIPT="scripts/release_control.py"
RESTORE_SCRIPT="scripts/restore_postgres.sh"
EVIDENCE_SCRIPT="scripts/pilot_evidence.py"
CAPABILITY_SCRIPT="scripts/pilot_release_capability.py"
ALERTMANAGER_RENDERER="scripts/render_alertmanager_config.py"
CURRENT_RELEASE_STATE="deploy/release/runtime/current_release.json"
ROLLBACK_REPORT="docs/pilot/rollback_drill_report.json"
ALERTMANAGER_SECRET_ENV="deploy/secrets/alertmanager.env"

for required_script in "$CONTROL_SCRIPT" "$RESTORE_SCRIPT" "$CAPABILITY_SCRIPT"; do
  if [ ! -f "$required_script" ]; then
    echo "Required rollback script is missing: $required_script" >&2
    exit 1
  fi
done

if [ "$ROLLBACK_DRILL" = "1" ]; then
  if [ ! -f "$EVIDENCE_SCRIPT" ]; then
    echo "Pilot evidence script is missing: $EVIDENCE_SCRIPT" >&2
    exit 1
  fi
  if [ -z "$BACKUP" ]; then
    echo "A rollback drill must restore a verified database backup" >&2
    exit 1
  fi
  if [ ! -f "$CURRENT_RELEASE_STATE" ]; then
    echo "Current release state is missing; cannot record rollback drill origin" >&2
    exit 1
  fi
  python3 "$EVIDENCE_SCRIPT" validate-secret --env .env >/dev/null
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

if [ "$ROLLBACK_DRILL" = "1" ]; then
  RECOVERY_SCOPE=$(python3 - "$BACKUP" <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from pilot_evidence import (  # noqa: E402
    RECOVERY_ENV_KEYS,
    _positive_seconds,
    _verified_backup_manifest,
    read_env_file,
    require_signing_secret,
)

backup = Path(sys.argv[1]).resolve()
env = read_env_file(Path(".env"))
for key in RECOVERY_ENV_KEYS:
    value = os.environ.get(key)
    if value is not None and value.strip():
        env[key] = value.strip()

scope = str(env.get("PILOT_RECOVERY_SCOPE", "ci")).strip().lower() or "ci"
if scope not in {"ci", "pilot_host"}:
    raise SystemExit("PILOT_RECOVERY_SCOPE must be ci or pilot_host")

if scope == "pilot_host":
    secret = require_signing_secret(env)
    host_id = str(env.get("PILOT_RECOVERY_HOST_ID", "")).strip()
    if not host_id:
        raise SystemExit("PILOT_RECOVERY_HOST_ID is required for pilot_host rollback drills")
    _positive_seconds(env.get("PILOT_RECOVERY_RTO_SECONDS"), "PILOT_RECOVERY_RTO_SECONDS")
    _positive_seconds(env.get("PILOT_RECOVERY_RPO_SECONDS"), "PILOT_RECOVERY_RPO_SECONDS")
    manifest_value = str(env.get("PILOT_RECOVERY_BACKUP_MANIFEST", "")).strip()
    manifest = Path(manifest_value).resolve() if manifest_value else Path(f"{backup}.manifest.json")
    _verified_backup_manifest(manifest, backup, secret)

print(scope)
PY
  )
  echo "Rollback drill evidence scope: $RECOVERY_SCOPE"
fi

python3 "$CONTROL_SCRIPT" verify --archive "$RELEASE" >/dev/null
python3 "$CAPABILITY_SCRIPT" inspect --archive "$RELEASE" >/dev/null
if [ -n "$BACKUP" ]; then
  bash scripts/verify_backup.sh "$BACKUP"
fi

# Pilot rollback must not recover the customer-facing API while silently losing
# provider-command dispatch, ingress, or the internal alerting plane. Validate
# the target archive before any downtime so an older/incomplete release fails closed.
python3 - "$RELEASE" <<'PY'
import sys
import zipfile
from pathlib import Path

archive = Path(sys.argv[1])
required = {
    "Dockerfile.ingress",
    "scripts/render_alertmanager_config.py",
    "scripts/run_provider_command_jobs.py",
    "deploy/monitoring/prometheus.yml",
    "deploy/monitoring/rules/flashin_pilot.yml",
    "deploy/grafana/provisioning/datasources/prometheus.yml",
    "docker-compose.yml",
    "docker-compose.production.yml",
}
with zipfile.ZipFile(archive, "r") as bundle:
    names = set(bundle.namelist())
missing = sorted(required - names)
if missing:
    raise SystemExit(
        "Rollback target is missing pilot operational infrastructure: "
        + ", ".join(missing)
    )
PY

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required for rollback" >&2
  exit 1
fi

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
cp "$CONTROL_SCRIPT" "$TMP_DIR/release_control.py"
cp "$RESTORE_SCRIPT" "$TMP_DIR/restore_postgres.sh"
chmod +x "$TMP_DIR/restore_postgres.sh"
if [ "$ROLLBACK_DRILL" = "1" ]; then
  cp "$EVIDENCE_SCRIPT" "$TMP_DIR/pilot_evidence.py"
  cp "$CURRENT_RELEASE_STATE" "$TMP_DIR/from_release.json"
fi

echo "Rolling back to verified runtime-guarded release: $RELEASE"
[ -z "$BACKUP" ] || echo "Database restore source: $BACKUP"
[ "$ROLLBACK_DRILL" != "1" ] || echo "Rollback drill evidence recording is enabled"

if [ "$ROLLBACK_DRILL" = "1" ]; then
  RECOVERY_STARTED_AT=$(python3 -c 'from datetime import UTC, datetime; print(datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"))')
fi

if docker compose ps --status running --services 2>/dev/null | grep -qx backend; then
  if docker compose exec -T backend test -f /app/scripts/pilot_runtime.py; then
    echo "Stopping pilot checkout runtime before rollback..."
    docker compose exec -T backend python scripts/pilot_runtime.py _stop \
      --reason "rollback started"
  fi
fi

docker compose down

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
  --exclude 'deploy/runtime/' \
  --exclude 'deploy/secrets/alertmanager.env' \
  --exclude 'docs/pilot/live_pilot_state.json' \
  --exclude 'docs/pilot/live_pilot_summary.md' \
  --exclude 'docs/pilot/integration_check_report.json' \
  --exclude 'docs/pilot/integration_check_report.md' \
  --exclude 'docs/pilot/rollback_drill_report.json' \
  --exclude 'docs/pilot/rollback_drill_report.md' \
  --exclude 'docs/pilot/pilot_admission_manifest.json' \
  --exclude 'docs/pilot/pilot_admission_manifest.md' \
  --exclude 'docs/readiness_gate_report.json' \
  --exclude 'docs/readiness_gate_report.md' \
  --exclude 'docs/pilot_live_gate_report.json' \
  --exclude 'docs/pilot_live_gate_report.md' \
  "$TMP_DIR/release/" ./

if [ ! -f "$ALERTMANAGER_RENDERER" ]; then
  echo "Rolled-back release is missing Alertmanager renderer" >&2
  exit 1
fi

app_env=$(python3 - <<'PY'
from pathlib import Path
value = "development"
for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line.startswith("APP_ENV="):
        value = line.split("=", 1)[1].strip().strip('"').strip("'") or "development"
        break
print(value.lower())
PY
)
if [ "$app_env" = "production" ]; then
  if [ ! -f "$ALERTMANAGER_SECRET_ENV" ]; then
    echo "Production rollback requires preserved $ALERTMANAGER_SECRET_ENV" >&2
    exit 1
  fi
  echo "Re-rendering Alertmanager config from preserved root-only receiver secret..."
  python3 "$ALERTMANAGER_RENDERER"
else
  echo "Non-production rollback uses the checked-in local Alertmanager null receiver."
fi

# Rebuild from the extracted target release. Without this step Compose could
# restart images created from the newer deployment and leave runtime code at the
# wrong version even though files and release pointers were rolled back. This
# includes the custom-built ingress image: edge/runtime versions must roll back together.
echo "Building rolled-back application and ingress images from the target release..."
# Immutable v17 compatibility marker retained for older capability inspectors:
# docker compose build backend frontend admin bot notification_worker scheduler
docker compose build \
  backend frontend admin bot notification_worker provider_command_jobs scheduler caddy

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
  bash "$TMP_DIR/restore_postgres.sh" --yes "$BACKUP"
else
  echo "Code-only rollback explicitly authorized; database was not modified."
fi

echo "Checking rollback migration compatibility..."
docker compose run --rm backend alembic -c backend/alembic.ini current
docker compose run --rm backend python scripts/check_transaction_integrity.py
docker compose run --rm backend python scripts/check_pilot_runtime_integrity.py

echo "Forcing restored pilot runtime to stopped before public services start..."
docker compose run --rm backend python scripts/pilot_runtime.py _stop \
  --reason "rollback database restored"

echo "Starting rolled-back production services, durable provider worker and monitoring..."
docker compose up -d \
  db backend frontend admin bot caddy \
  notification_worker provider_command_jobs scheduler meilisearch \
  alertmanager prometheus grafana

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

alertmanager_ready=0
for _ in $(seq 1 60); do
  if docker compose exec -T backend curl -fsS http://alertmanager:9093/-/ready >/dev/null 2>&1; then
    alertmanager_ready=1
    break
  fi
  sleep 2
done
if [ "$alertmanager_ready" -ne 1 ]; then
  echo "Alertmanager did not become ready after rollback" >&2
  docker compose logs alertmanager
  exit 1
fi

prometheus_ready=0
for _ in $(seq 1 60); do
  rules_payload="$(
    docker compose exec -T backend curl -fsS http://prometheus:9090/api/v1/rules 2>/dev/null || true
  )"
  alertmanagers_payload="$(
    docker compose exec -T backend curl -fsS http://prometheus:9090/api/v1/alertmanagers 2>/dev/null || true
  )"
  if printf '%s' "$rules_payload" | grep -q 'FlashinPilotRuntimeStopped' \
    && printf '%s' "$alertmanagers_payload" | grep -q 'alertmanager:9093'; then
    prometheus_ready=1
    break
  fi
  sleep 2
done
if [ "$prometheus_ready" -ne 1 ]; then
  echo "Prometheus did not restore pilot rules or Alertmanager discovery" >&2
  docker compose logs prometheus alertmanager
  exit 1
fi

grafana_ready=0
for _ in $(seq 1 60); do
  if docker compose exec -T backend curl -fsS http://grafana:3000/api/health >/dev/null 2>&1; then
    grafana_ready=1
    break
  fi
  sleep 2
done
if [ "$grafana_ready" -ne 1 ]; then
  echo "Grafana did not become healthy after rollback" >&2
  docker compose logs grafana
  exit 1
fi

for service in \
  db backend frontend admin bot caddy \
  notification_worker provider_command_jobs scheduler meilisearch \
  alertmanager prometheus grafana; do
  if ! docker compose ps --status running --services | grep -qx "$service"; then
    echo "$service is not running after rollback" >&2
    docker compose logs "$service"
    exit 1
  fi
done

docker compose exec -T backend python scripts/container_smoke.py
python3 "$TMP_DIR/release_control.py" promote \
  --archive "$RELEASE" \
  --state-dir "$RELEASE_STATE_DIR" >/dev/null
PROMOTED_RELEASE=$(python3 "$TMP_DIR/release_control.py" resolve \
  --slot current \
  --state-dir "$RELEASE_STATE_DIR")
if [ "$PROMOTED_RELEASE" != "$RELEASE" ]; then
  echo "Rollback release pointer promotion mismatch: expected '$RELEASE', got '$PROMOTED_RELEASE'" >&2
  exit 1
fi
python3 scripts/pilot_release_capability.py stamp --slot current --env .env >/dev/null
python3 scripts/pilot_release_capability.py verify --slot both --env .env

if [ "$ROLLBACK_DRILL" = "1" ]; then
  max_age_days=$(python3 - <<'PY'
from pathlib import Path
value = "30"
for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line.startswith("PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS="):
        value = line.split("=", 1)[1].strip().strip('"').strip("'") or "30"
        break
print(value)
PY
)
  python3 "$TMP_DIR/pilot_evidence.py" record-rollback \
    --env "$(pwd)/.env" \
    --from-release-state "$TMP_DIR/from_release.json" \
    --to-release-state "$(pwd)/$CURRENT_RELEASE_STATE" \
    --backup "$BACKUP" \
    --report "$(pwd)/$ROLLBACK_REPORT" \
    --max-age-days "$max_age_days" \
    --recovery-scope "$RECOVERY_SCOPE" \
    --started-at "$RECOVERY_STARTED_AT"
fi

echo "Rollback completed and release pointer promoted: $RELEASE"
echo "Provider-command worker and internal monitoring were restored and verified."
echo "Pilot runtime remains stopped; a fresh admission is required before resume."
