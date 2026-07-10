#!/usr/bin/env python3
from pathlib import Path
import json

items = {
    "one_command_launch": Path("scripts/launch.py").exists(),
    "simple_wrapper": Path("scripts/start_simple.sh").exists(),
    "env_local": Path(".env.local.example").exists(),
    "env_production": Path(".env.production.example").exists(),
    "preflight": Path("scripts/preflight.py").exists(),
    "readiness_gate": Path("scripts/readiness_gate.py").exists(),
    "integration_check": Path("scripts/check_integrations.py").exists(),
    "pilot_sheet": Path("docs/20_order_pilot_sheet.csv").exists(),
    "runbook_index": Path("docs/runbook_index.md").exists(),
    "developer_handover": Path("docs/developer_handover.md").exists(),
    "incident_templates": Path("docs/incident_templates/payment_incident.md").exists(),
}
score = sum(items.values())
total = len(items)
report = {"score": score, "total": total, "percent": round(score / total * 100, 2), "items": items}
Path("docs/simplicity_score.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
Path("docs/simplicity_score.md").write_text(
    "# Simplicity Score\n\n"
    f"{score}/{total} ({report['percent']}%)\n\n"
    + "\n".join([f"- [{'x' if ok else ' '}] {key}" for key, ok in items.items()])
    + "\n",
    encoding="utf-8",
)
print(report)
