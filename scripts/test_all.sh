#!/usr/bin/env bash
set -euo pipefail

echo "Running package checks..."
python3 scripts/preflight.py
python3 -m compileall -q backend bot

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo "Running tests in backend container..."
  docker compose run --rm backend pytest -q backend/tests
else
  echo "Docker not available. Running local pytest if dependencies are installed."
  pytest -q backend/tests
fi
