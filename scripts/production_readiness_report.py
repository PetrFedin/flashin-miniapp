#!/usr/bin/env python3
from pathlib import Path
import json

items = {
    "env_exists": Path(".env").exists(),
    "docker_compose": Path("docker-compose.yml").exists(),
    "migrations": bool(list(Path("backend/alembic/versions").glob("*.py"))),
    "legal_offer": Path("frontend/public/legal/offer.html").exists(),
    "legal_privacy": Path("frontend/public/legal/privacy.html").exists(),
    "legal_returns": Path("frontend/public/legal/returns.html").exists(),
    "backup_script": Path("scripts/backup_postgres.sh").exists(),
    "restore_script": Path("scripts/restore_postgres.sh").exists(),
    "deploy_script": Path("scripts/deploy_production.sh").exists(),
    "rollback_script": Path("scripts/rollback.sh").exists(),
    "preflight": Path("scripts/preflight.py").exists(),
    "e2e_smoke": Path("tests/e2e_smoke.py").exists(),
    "runbooks": Path("docs/runbook_index.md").exists(),
}

score = sum(1 for v in items.values() if v)
total = len(items)
report = {
    "score": score,
    "total": total,
    "percent": round(score / total * 100, 2),
    "items": items,
}

Path("docs/production_readiness_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
Path("docs/production_readiness_report.md").write_text(
    "# Production Readiness Report\n\n"
    f"Score: {score}/{total} ({report['percent']}%)\n\n"
    + "\n".join([f"- [{'x' if ok else ' '}] {key}" for key, ok in items.items()])
    + "\n",
    encoding="utf-8",
)
print(report)
