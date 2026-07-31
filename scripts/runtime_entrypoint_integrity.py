#!/usr/bin/env python3
"""Fail fast when build and migration entrypoints are disconnected."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors: list[dict[str, str]] = []

    for app in ("frontend", "admin"):
        index = root / app / "index.html"
        source = index.read_text(encoding="utf-8") if index.is_file() else ""
        if not source:
            errors.append(
                {
                    "check": "vite_entrypoint",
                    "path": str(index),
                    "message": "index.html is missing",
                }
            )
            continue
        if 'id="root"' not in source and "id='root'" not in source:
            errors.append(
                {
                    "check": "vite_entrypoint",
                    "path": str(index),
                    "message": "root mount node is missing",
                }
            )
        if "/src/main.jsx" not in source:
            errors.append(
                {
                    "check": "vite_entrypoint",
                    "path": str(index),
                    "message": "main.jsx module entry is missing",
                }
            )

    alembic_ini = root / "backend" / "alembic.ini"
    alembic_source = alembic_ini.read_text(encoding="utf-8") if alembic_ini.is_file() else ""
    if "script_location = %(here)s/alembic" not in alembic_source:
        errors.append(
            {
                "check": "alembic_entrypoint",
                "path": str(alembic_ini),
                "message": "script_location must be anchored to %(here)s for root and Docker invocations",
            }
        )

    migrate = root / "scripts" / "migrate.sh"
    migrate_source = migrate.read_text(encoding="utf-8") if migrate.is_file() else ""
    if "backend/alembic.ini" not in migrate_source:
        errors.append(
            {
                "check": "migration_command",
                "path": str(migrate),
                "message": "production migration command is not connected to backend/alembic.ini",
            }
        )

    report = {"status": "failed" if errors else "ok", "errors": errors}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
