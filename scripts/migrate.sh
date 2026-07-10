#!/usr/bin/env bash
set -euo pipefail

echo "Running Alembic migrations..."
docker compose run --rm backend alembic -c backend/alembic.ini upgrade head
echo "Migrations completed"
