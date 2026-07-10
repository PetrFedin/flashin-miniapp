#!/usr/bin/env bash
set -euo pipefail

echo "Security audit started"

echo "Python dependencies:"
if command -v pip-audit >/dev/null; then
  pip-audit -r backend/requirements.txt || true
else
  echo "pip-audit not installed. Install: pip install pip-audit"
fi

echo "Secret scan:"
if command -v gitleaks >/dev/null; then
  gitleaks detect --no-git --source . || true
else
  echo "gitleaks not installed. Install from https://github.com/gitleaks/gitleaks"
fi

echo "Docker image scan:"
if command -v trivy >/dev/null; then
  trivy fs . || true
else
  echo "trivy not installed. Install from https://github.com/aquasecurity/trivy"
fi

echo "Security audit completed"
