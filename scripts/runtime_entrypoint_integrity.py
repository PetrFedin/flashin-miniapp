#!/usr/bin/env python3
"""Fail fast when build and migration entrypoints are disconnected."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

REVISION_PATTERN = re.compile(r'^revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
VERSION_COLUMN_WIDENING = "ALTER COLUMN version_num TYPE VARCHAR(255)"


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
    required_alembic_links = {
        "script_location = %(here)s/alembic": "migration scripts must be anchored to the Alembic config directory",
        "prepend_sys_path = %(here)s/..": "project root must be available for backend package imports",
    }
    for expected, message in required_alembic_links.items():
        if expected not in alembic_source:
            errors.append(
                {
                    "check": "alembic_entrypoint",
                    "path": str(alembic_ini),
                    "message": message,
                }
            )

    versions_dir = root / "backend" / "alembic" / "versions"
    migrations: list[tuple[Path, str, str]] = []
    for path in sorted(versions_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        match = REVISION_PATTERN.search(source)
        if not match:
            errors.append(
                {
                    "check": "alembic_revision",
                    "path": str(path),
                    "message": "revision identifier is missing",
                }
            )
            continue
        migrations.append((path, match.group(1), source))

    for revision, count in Counter(item[1] for item in migrations).items():
        if count > 1:
            errors.append(
                {
                    "check": "alembic_revision",
                    "path": str(versions_dir),
                    "message": f"duplicate revision identifier: {revision}",
                }
            )
        if len(revision) > 255:
            errors.append(
                {
                    "check": "alembic_revision",
                    "path": str(versions_dir),
                    "message": f"revision identifier exceeds VARCHAR(255): {revision}",
                }
            )

    first_long_index = next(
        (index for index, (_, revision, _) in enumerate(migrations) if len(revision) > 32),
        None,
    )
    widening_index = next(
        (index for index, (_, _, source) in enumerate(migrations) if VERSION_COLUMN_WIDENING in source),
        None,
    )
    if first_long_index is not None and (widening_index is None or widening_index > first_long_index):
        path, revision, _ = migrations[first_long_index]
        errors.append(
            {
                "check": "alembic_version_capacity",
                "path": str(path),
                "message": (
                    f"revision {revision} exceeds Alembic's default VARCHAR(32) before "
                    "the version column is widened"
                ),
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
