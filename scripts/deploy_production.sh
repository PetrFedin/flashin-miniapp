#!/usr/bin/env bash
set -euo pipefail

echo "FLASHIN production deploy"

if [ ! -f .env ]; then
  echo ".env is missing. Copy .env.production.example to .env and fill secrets."
  exit 1
fi

export COMPOSE_FILE="docker-compose.yml:docker-compose.production.yml"

python3 scripts/preflight.py

echo "Building images..."
docker compose build

echo "Starting database..."
docker compose up -d db

echo "Waiting for PostgreSQL..."
db_ready=0
for _ in $(seq 1 60); do
  if docker compose exec -T db pg_isready -U flashin -d flashin >/dev/null 2>&1; then
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

schema_exists="$(docker compose exec -T db psql -U flashin -d flashin -tAc "SELECT to_regclass('public.alembic_version') IS NOT NULL" | tr -d '[:space:]')"
if [ "$schema_exists" = "t" ]; then
  echo "Running pre-migration transaction integrity audit..."
  docker compose run --rm backend python scripts/check_transaction_integrity.py

  echo "Backing up database before migration..."
  scripts/backup_postgres.sh
else
  echo "First deploy detected: no existing Alembic schema to back up or audit"
  docker compose run --rm backend python scripts/check_transaction_integrity.py --allow-missing-schema
fi

echo "Running migrations..."
docker compose run --rm backend alembic -c backend/alembic.ini upgrade head

echo "Verifying transaction integrity after migration..."
docker compose run --rm backend python scripts/check_transaction_integrity.py

echo "Starting production services and financial workers..."
docker compose \
  --profile production \
  --profile workers \
  --profile scheduler \
  up -d \
  backend frontend admin bot caddy notification_worker scheduler

echo "Running health checks..."
backend_healthy=0
for _ in $(seq 1 90); do
  if curl -fsS http://localhost:8000/health >/dev/null; then
    backend_healthy=1
    echo "Backend healthy"
    break
  fi
  sleep 2
done
if [ "$backend_healthy" -ne 1 ]; then
  echo "Backend did not become healthy"
  docker compose logs backend
  exit 1
fi

for service in notification_worker scheduler; do
  if ! docker compose ps --status running --services | grep -qx "$service"; then
    echo "$service is not running"
    docker compose logs "$service"
    exit 1
  fi
done

python3 tests/e2e_smoke.py

echo "Deploy completed"
