#!/usr/bin/env python3
from pathlib import Path
import json
import sys

budgets = {
    "frontend_dist_mb": 8,
    "admin_dist_mb": 8,
    "zip_mb": 50,
}

def size_mb(path: Path) -> float:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size / 1024 / 1024
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024 / 1024

report = {
    "frontend_dist_mb": round(size_mb(Path("frontend/dist")), 2),
    "admin_dist_mb": round(size_mb(Path("admin/dist")), 2),
}

failed = []
for key, budget in budgets.items():
    if key in report and report[key] > budget:
        failed.append({"metric": key, "actual": report[key], "budget": budget})

Path("docs/performance_budget_report.json").write_text(json.dumps({"report": report, "failed": failed}, indent=2), encoding="utf-8")
print({"report": report, "failed": failed})
sys.exit(1 if failed else 0)
