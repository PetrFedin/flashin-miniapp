#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be configured}"

echo "Running FLASHIN production admission migrations..."
alembic -c backend/alembic.ini upgrade head
echo "Alembic heads:"
alembic -c backend/alembic.ini current
