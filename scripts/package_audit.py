#!/usr/bin/env python3
from pathlib import Path
import json
import subprocess

ROOT = Path(".")
required = {
    "core": [
        "backend/main.py", "backend/models.py", "backend/schemas.py", "backend/config.py",
        "backend/database.py", "backend/alembic.ini", "docker-compose.yml",
    ],
    "apps": [
        "frontend/src/App.js", "frontend/package.json",
        "admin/src/main.jsx", "admin/package.json",
        "bot/main.py",
    ],
    "launch": [
        "scripts/launch.py", "scripts/start_simple.sh", "scripts/preflight.py",
        "scripts/readiness_gate.py", "scripts/check_integrations.py",
        "scripts/test_all.sh",
    ],
    "integrations": [
        "backend/api/payments.py", "backend/api/moysklad.py",
        "backend/api/delivery_providers.py", "backend/api/payment_reconciliation.py",
        "backend/api/fulfillment.py", "backend/api/admin_security.py",
    ],
    "ops": [
        "scripts/backup_postgres.sh", "scripts/restore_postgres.sh",
        "scripts/deploy_production.sh", "scripts/rollback.sh",
        "deploy/grafana/dashboards/flashin_operations.json",
    ],
    "docs": [
        "README.md", "docs/v49_unified_system_map.md", "docs/v50_final_handover.md",
        "docs/v51_pilot_freeze_layer.md", "docs/acceptance/pilot_acceptance_signoff.md",
    ],
}

report = {}
for section, files in required.items():
    missing = [f for f in files if not (ROOT / f).exists()]
    report[section] = {"ok": not missing, "missing": missing, "checked": len(files)}

def run(cmd):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return {"ok": res.returncode == 0, "stdout": res.stdout[-2000:], "stderr": res.stderr[-2000:]}

report["preflight"] = run("python3 scripts/preflight.py --source-only")
report["compile"] = run("python3 -m compileall -q backend bot")

Path("docs/audit/package_audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
Path("docs/audit/package_audit_report.md").write_text(
    "# Package Audit Report\n\n"
    + "\n".join([f"- [{'x' if v.get('ok') else ' '}] {k}" for k, v in report.items()])
    + "\n",
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False, indent=2))
bad = [k for k, v in report.items() if not v.get("ok")]
raise SystemExit(1 if bad else 0)
