#!/usr/bin/env python3
from pathlib import Path
import json
import subprocess
import sys

checks = []

def add(name, ok, critical=True, detail=""):
    checks.append({"name": name, "ok": bool(ok), "critical": critical, "detail": detail})

def run(name, cmd, critical=True):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90)
        add(name, res.returncode == 0, critical, (res.stdout + "\n" + res.stderr)[-1000:])
    except Exception as exc:
        add(name, False, critical, str(exc))

root = Path(".")
add("env_file_exists", (root / ".env").exists(), True, ".env must exist for real launch")
add("legal_offer_exists", (root / "frontend/public/legal/offer.html").exists(), True)
add("legal_privacy_exists", (root / "frontend/public/legal/privacy.html").exists(), True)
add("legal_returns_exists", (root / "frontend/public/legal/returns.html").exists(), True)
add("docker_compose_exists", (root / "docker-compose.yml").exists(), True)
add("migrations_exist", bool(list((root / "backend/alembic/versions").glob("*.py"))), True)
add("runbook_index_exists", (root / "docs/runbook_index.md").exists(), True)

run("preflight", "python3 scripts/preflight.py", True)
run("env_validation", "python3 scripts/validate_env.py", True)
run("production_readiness_report", "python3 scripts/production_readiness_report.py", False)
run("e2e_smoke_if_running", "python3 tests/e2e_smoke.py", False)

critical_failed = [c for c in checks if c["critical"] and not c["ok"]]
report = {
    "go": len(critical_failed) == 0,
    "critical_failed": critical_failed,
    "checks": checks,
}
Path("docs/readiness_gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
Path("docs/readiness_gate_report.md").write_text(
    "# Readiness Gate Report\n\n"
    + ("GO\n\n" if report["go"] else "NO-GO\n\n")
    + "\n".join([f"- [{'x' if c['ok'] else ' '}] {c['name']} ({'critical' if c['critical'] else 'optional'})" for c in checks])
    + "\n",
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if report["go"] else 1)
