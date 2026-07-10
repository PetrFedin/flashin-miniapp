#!/usr/bin/env python3
from pathlib import Path
import json

ROOT = Path(".")
checks = {
    "telegram_miniapp": [
        "frontend/src/App.js",
        "bot/main.py",
        "backend/api/auth.py",
    ],
    "catalog_cart_checkout": [
        "backend/api/products.py",
        "backend/api/cart.py",
        "backend/api/orders.py",
        "frontend/src/App.js",
    ],
    "payments_refunds": [
        "backend/api/payments.py",
        "backend/api/returns.py",
        "backend/api/payment_reconciliation.py",
    ],
    "moysklad": [
        "backend/services/moysklad.py",
        "backend/api/moysklad.py",
        "backend/api/moysklad_deep_mapping.py",
    ],
    "delivery": [
        "backend/api/delivery_providers.py",
        "backend/api/delivery_quotes.py",
    ],
    "fulfillment_sla": [
        "backend/api/fulfillment.py",
        "backend/jobs/sla_jobs.py",
    ],
    "loyalty_referral_crm": [
        "backend/api/loyalty.py",
        "backend/api/crm.py",
        "backend/services/loyalty.py",
    ],
    "support_privacy": [
        "backend/api/support.py",
        "backend/api/privacy.py",
    ],
    "media": [
        "backend/api/media.py",
        "backend/jobs/media_jobs.py",
        "frontend/public/fallback-product.svg",
    ],
    "observability": [
        "backend/middleware/metrics.py",
        "deploy/grafana/dashboards/flashin_operations.json",
        "deploy/grafana/dashboards/flashin_payments.json",
        "deploy/grafana/dashboards/flashin_fulfillment.json",
    ],
    "launch_ops": [
        "scripts/launch.py",
        "scripts/preflight.py",
        "scripts/readiness_gate.py",
        "scripts/deploy_production.sh",
        "scripts/rollback.sh",
    ],
}

report = {}
for area, files in checks.items():
    missing = [f for f in files if not (ROOT / f).exists()]
    report[area] = {"ok": not missing, "missing": missing, "files": files}

Path("docs/connected_system_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
Path("docs/connected_system_audit.md").write_text(
    "# Connected System Audit\n\n"
    + "\n".join([f"- [{'x' if v['ok'] else ' '}] {k}" for k, v in report.items()])
    + "\n",
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False, indent=2))
bad = [k for k, v in report.items() if not v["ok"]]
raise SystemExit(1 if bad else 0)
