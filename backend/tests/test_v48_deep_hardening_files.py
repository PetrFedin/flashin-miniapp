from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_v48_files_exist():
    for path in [
        "backend/middleware/security_headers.py",
        "backend/api/admin_security.py",
        "backend/api/delivery_quotes.py",
        "backend/services/moysklad_deep_mapping_v2.py",
        "backend/services/cdn.py",
        "backend/tests/e2e/test_real_order_flow_runner.py",
        "frontend/public/fallback-product.svg",
        "admin/src/adminEndpoints.js",
        "deploy/grafana/dashboards/flashin_payments.json",
        "deploy/grafana/dashboards/flashin_fulfillment.json",
        "docs/v48_deep_hardening.md",
    ]:
        assert (ROOT / path).exists()
