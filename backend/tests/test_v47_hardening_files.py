from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_v47_files_exist():
    for path in [
        "backend/api/payment_reconciliation.py",
        "backend/api/delivery_providers.py",
        "backend/api/moysklad_deep_mapping.py",
        "backend/services/admin_security.py",
        "backend/jobs/media_jobs.py",
        "scripts/run_media_jobs.py",
        "scripts/security_audit.sh",
        "deploy/grafana/dashboards/flashin_operations.json",
        "deploy/loadtest/k6_catalog_search_checkout.js",
        "deploy/loadtest/k6_webhook_burst.js",
        "backend/alembic/versions/0009_security_payment_delivery_media_hardening.py",
    ]:
        assert (ROOT / path).exists()
