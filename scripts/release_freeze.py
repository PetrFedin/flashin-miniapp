#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import json
import hashlib

ROOT = Path(".")
freeze_files = [
    "README.md",
    "docker-compose.yml",
    "Dockerfile.backend",
    "Dockerfile.frontend",
    "Dockerfile.admin",
    "Dockerfile.bot",
    "backend/main.py",
    "backend/models.py",
    "frontend/src/App.js",
    "admin/src/main.jsx",
]

manifest = {
    "version": "v51",
    "frozen_at": datetime.utcnow().isoformat(),
    "files": {},
    "rule": "Do not change architecture before live pilot. Only fix pilot-blocking bugs.",
}

for rel in freeze_files:
    p = ROOT / rel
    if p.exists():
        manifest["files"][rel] = hashlib.sha256(p.read_bytes()).hexdigest()

Path("deploy/release/v51_freeze_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
Path("docs/acceptance/v51_release_freeze.md").write_text(
    "# v51 Release Freeze\n\n"
    f"Frozen at: {manifest['frozen_at']}\n\n"
    "Rule: do not add new architecture before live pilot. Only fix pilot-blocking bugs.\n\n"
    "## Frozen files\n\n"
    + "\n".join([f"- `{k}`" for k in manifest["files"].keys()])
    + "\n",
    encoding="utf-8",
)
print({"written": "deploy/release/v51_freeze_manifest.json", "files": len(manifest["files"])})
