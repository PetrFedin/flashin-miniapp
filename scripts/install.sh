#!/usr/bin/env bash
set -euo pipefail

echo "FLASHIN v41 installer"

command -v docker >/dev/null || { echo "Docker is required"; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose plugin is required"; exit 1; }

if [ ! -f .env ]; then
  cp .env.local.example .env
  echo "Created .env"
fi

python3 scripts/ensure_webhook_secret.py .env
python3 scripts/preflight.py --require-env

echo "Building containers..."
docker compose build

echo "Starting database..."
docker compose up -d db

echo "Running migrations..."
docker compose run --rm backend alembic -c backend/alembic.ini upgrade head

echo "Starting app..."
docker compose up -d backend frontend admin bot

echo "Health check..."
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null; then
    echo "Install complete"
    echo "Mini App: http://localhost:5173"
    echo "Admin: http://localhost:5174"
    echo "API: http://localhost:8000/docs"
    exit 0
  fi
  sleep 2
done

echo "Backend did not become healthy"
exit 1
