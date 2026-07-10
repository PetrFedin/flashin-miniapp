#!/usr/bin/env bash
set -euo pipefail
curl -fsS http://localhost:8000/health >/dev/null
curl -fsS http://localhost:8000/ready >/dev/null
echo "Health OK"
