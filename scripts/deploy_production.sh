#!/usr/bin/env bash
set -euo pipefail

echo "FLASHIN production deploy"

if [ ! -f .env ]; then
  echo ".env is missing. Copy .env.production.example to .env and fill secrets."
  exit 1
fi

python3 scripts/preflight.py --require-env

echo "Building images..."
docker compose build

echo "Backing up database before migration..."
scripts/backup_postgres.sh || echo "Backup failed or database not running yet; continue only if first deploy."

echo "Starting database..."
docker compose up -d db

echo "Running migrations..."
docker compose run --rm backend alembic -c backend/alembic.ini upgrade head

echo "Starting production services..."
docker compose --profile production up -d backend frontend admin bot caddy

echo "Running health checks..."
for i in $(seq 1 90); do
  if curl -fsS http://localhost:8000/health >/dev/null; then
    echo "Backend healthy"
    break
  fi
  sleep 2
done

python3 tests/e2e_smoke.py

echo "Deploy completed"
