#!/usr/bin/env bash
set -euo pipefail

echo "FLASHIN production deploy"

if [ ! -f .env ]; then
  echo ".env is missing. Copy .env.production.example to .env and fill secrets."
  exit 1
fi

export COMPOSE_FILE="docker-compose.yml:docker-compose.production.yml"
export COMPOSE_PROFILES="production,workers,scheduler,search"

python3 scripts/validate_env.py
python3 scripts/preflight.py

echo "Validating production Compose configuration..."
docker compose config --quiet
python3 scripts/check_production_compose.py

echo "Building images..."
docker compose build

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
  scripts/backup_postgres.sh
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

echo "Starting production services, scheduler, notifications and search..."
docker compose up -d db backend frontend admin bot caddy notification_worker scheduler meilisearch

echo "Waiting for backend inside Docker network..."
backend_healthy=0
for _ in $(seq 1 90); do
  if docker compose exec -T backend curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
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

for service in db backend frontend admin bot caddy notification_worker scheduler meilisearch; do
  if ! docker compose ps --status running --services | grep -qx "$service"; then
    echo "$service is not running"
    docker compose logs "$service"
    exit 1
  fi
done

echo "Running internal smoke checks..."
docker compose exec -T backend python scripts/container_smoke.py

echo "Deploy completed"
