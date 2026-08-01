#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path

from script_time import utc_timestamp

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
    "frozen_at": utc_timestamp(),
    "files": {},
    "rule": "Do not change architecture before live pilot. Only fix pilot-blocking bugs.",
}

for relative_path in freeze_files:
    path = ROOT / relative_path
    if path.exists():
        manifest["files"][relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()

Path("deploy/release/v51_freeze_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
Path("docs/acceptance/v51_release_freeze.md").write_text(
    "# v51 Release Freeze\n\n"
    f"Frozen at: {manifest['frozen_at']}\n\n"
    "Rule: do not add new architecture before live pilot. Only fix pilot-blocking bugs.\n\n"
    "## Frozen files\n\n"
    + "\n".join([f"- `{relative_path}`" for relative_path in manifest["files"]])
    + "\n",
    encoding="utf-8",
)
print(
    {
        "written": "deploy/release/v51_freeze_manifest.json",
        "files": len(manifest["files"]),
    }
)
