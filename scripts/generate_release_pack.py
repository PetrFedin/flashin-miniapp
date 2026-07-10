#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import json

release = {
    "version": "v50",
    "created_at": datetime.utcnow().isoformat(),
    "entrypoints": {
        "simple_start": "scripts/start_simple.sh",
        "unified_launch": "scripts/launch.py",
        "production_deploy": "scripts/deploy_production.sh",
        "rollback": "scripts/rollback.sh",
        "readiness_gate": "scripts/readiness_gate.py",
    },
    "must_read": [
        "README.md",
        "docs/v49_unified_system_map.md",
        "docs/v49_final_gap_analysis.md",
        "docs/v50_final_handover.md",
        "docs/pilot/live_pilot_runner.md",
    ],
    "no_go_if": [
        "Telegram auth fails",
        "YooKassa webhook fails",
        "MoySklad stock mapping is wrong",
        "Refund does not return money/points",
        "Fulfillment task is not created after payment",
        "Backup restore was not tested",
    ],
}

Path("deploy/release/v50_release_pack.json").write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8")
Path("docs/v50_release_pack.md").write_text(
    "# FLASHIN v50 Release Pack\n\n"
    + f"Created at: {release['created_at']}\n\n"
    + "## Entrypoints\n\n"
    + "\n".join([f"- {k}: `{v}`" for k, v in release["entrypoints"].items()])
    + "\n\n## Must read\n\n"
    + "\n".join([f"- `{x}`" for x in release["must_read"]])
    + "\n\n## No-go if\n\n"
    + "\n".join([f"- {x}" for x in release["no_go_if"]])
    + "\n",
    encoding="utf-8",
)
print({"written": "docs/v50_release_pack.md"})
