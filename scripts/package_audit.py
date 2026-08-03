#!/usr/bin/env python3
from pathlib import Path
import json
import subprocess

ROOT = Path(".")
required = {
    "core": [
        "backend/main.py", "backend/models.py", "backend/schemas.py", "backend/config.py",
        "backend/database.py", "backend/pilot_models.py", "backend/alembic.ini",
        "backend/alembic/versions/0022_pilot_runtime_guard.py", "docker-compose.yml",
    ],
    "apps": [
        "frontend/src/App.js", "frontend/package.json",
        "admin/src/main.jsx", "admin/package.json",
        "admin/src/BusinessEventsPanel.jsx", "admin/src/PilotOperationsPanel.jsx",
        "admin/src/pilotOperations.js", "admin/src/pilotOperations.test.js",
        "bot/main.py",
    ],
    "launch": [
        "scripts/launch.py", "scripts/start_simple.sh", "scripts/preflight.py",
        "scripts/readiness_gate.py", "scripts/check_integrations.py",
        "scripts/pilot_evidence.py", "scripts/pilot_admission.py", "scripts/pilot_runtime.py",
        "scripts/pilot_release_capability.py", "scripts/check_pilot_runtime_integrity.py",
        "scripts/test_all.sh", "scripts/pilot_control.py", "scripts/pilot_runner.py",
        "scripts/generate_20_order_pilot_sheet.py", "scripts/release_control.py",
        "backend/tests/test_pilot_control.py", "backend/tests/test_pilot_readiness.py",
        "backend/tests/test_integration_checks.py", "backend/tests/test_release_control.py",
        "backend/tests/test_release_shell_safety.py", "backend/tests/test_pilot_evidence.py",
        "backend/tests/test_pilot_admission.py", "backend/tests/test_pilot_runtime.py",
        "backend/tests/test_pilot_runtime_cli.py", "backend/tests/test_pilot_runtime_wiring.py",
        "backend/tests/test_pilot_release_capability.py",
        "backend/tests/test_pilot_runtime_integrity.py",
        "backend/tests/test_pilot_payment_circuit_breaker.py",
        "backend/tests/test_pilot_circuit_breaker_wiring.py",
        "backend/tests/test_pilot_money_safety_fail_closed.py",
        "backend/tests/test_pilot_operations_observability.py",
    ],
    "integrations": [
        "backend/api/payments.py", "backend/api/returns.py", "backend/api/moysklad.py",
        "backend/api/delivery_providers.py", "backend/api/payment_reconciliation.py",
        "backend/api/fulfillment.py", "backend/api/admin_security.py",
        "backend/services/pilot_runtime.py", "backend/services/pilot_circuit_breaker.py",
        "backend/services/payment_reconciliation.py",
        "scripts/check_telegram_bot.py", "scripts/check_yookassa_test.py",
        "scripts/check_moysklad.py", "scripts/check_r2_s3.py",
        "scripts/check_meilisearch.py",
    ],
    "ops": [
        "backend/api/ops.py", "backend/services/pilot_observability.py",
        "admin/src/PilotOperationsPanel.jsx", "admin/src/pilotOperations.js",
        "scripts/backup_postgres.sh", "scripts/verify_backup.sh",
        "scripts/restore_postgres.sh", "scripts/deploy_production.sh",
        "scripts/rollback.sh", "scripts/release_control.py",
        "scripts/check_pilot_runtime_integrity.py",
        "docs/pilot/release_and_rollback_runbook.md",
        "docs/pilot/pilot_runtime_guard.md",
        "docs/pilot/pilot_payment_circuit_breaker.md",
        "docs/pilot/pilot_operations_observability.md",
        "docs/pilot/pilot_admin_operations_panel.md",
        "deploy/grafana/dashboards/flashin_operations.json",
    ],
    "docs": [
        "README.md", "docs/v49_unified_system_map.md", "docs/v50_final_handover.md",
        "docs/v51_pilot_freeze_layer.md", "docs/acceptance/pilot_acceptance_signoff.md",
        "docs/pilot/pilot_launch_runbook.md", "docs/pilot/provider_probe_runbook.md",
        "docs/pilot/provider_evidence_and_admission.md", "docs/pilot/pilot_runtime_guard.md",
        "docs/pilot/pilot_payment_circuit_breaker.md",
        "docs/pilot/pilot_operations_observability.md",
        "docs/pilot/pilot_admin_operations_panel.md",
    ],
}

report = {}
for section, files in required.items():
    missing = [f for f in files if not (ROOT / f).exists()]
    report[section] = {"ok": not missing, "missing": missing, "checked": len(files)}


def run(cmd):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return {"ok": res.returncode == 0, "stdout": res.stdout[-2000:], "stderr": res.stderr[-2000:]}


report["preflight"] = run("python3 scripts/preflight.py")
report["compile"] = run("python3 -m compileall -q backend bot scripts")
report["shell_syntax"] = run("find scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n")

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
