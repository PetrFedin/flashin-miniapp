#!/usr/bin/env bash
set -euo pipefail

if [ ! -f .env ]; then
  cp .env.local.example .env
  echo "Created .env from .env.local.example"
  echo "Fill TELEGRAM_BOT_TOKEN, JWT_SECRET and payment/MoySklad settings before public launch."
fi

python3 scripts/preflight.py

docker compose build

docker compose up -d db
echo "Waiting for database..."
sleep 5

docker compose run --rm backend alembic -c backend/alembic.ini upgrade head

docker compose up -d backend frontend admin bot

echo "Waiting for backend health..."
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null; then
    echo "Backend is healthy"
    break
  fi
  sleep 2
done

echo "FLASHIN v40 is running:"
echo "Mini App: http://localhost:5173"
echo "Admin:    http://localhost:5174"
echo "API:      http://localhost:8000"
