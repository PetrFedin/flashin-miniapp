#!/usr/bin/env bash
set -euo pipefail

HOST=${POSTGRES_HOST:-db}
PORT=${POSTGRES_PORT:-5432}

echo "Waiting for PostgreSQL at $HOST:$PORT..."
for i in $(seq 1 60); do
  if nc -z "$HOST" "$PORT"; then
    echo "PostgreSQL is ready"
    exit 0
  fi
  sleep 1
done

echo "PostgreSQL is not ready after 60 seconds"
exit 1
