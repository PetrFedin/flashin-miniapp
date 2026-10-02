#!/usr/bin/env python3
"""Sanitized PostgreSQL migration-state probe for production admission.

Reads DATABASE_URL from the environment. Never emits credentials, hostname,
port, database name, or raw database exception text.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.services.database_readiness import (
    current_migration_heads,
    expected_migration_heads,
)


def _scheme(value: str) -> str:
    candidate = str(value or "").strip()
    if "://" not in candidate:
        return ""
    return candidate.split("://", 1)[0].lower()


def build_report(database_url: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "flashin_database_migration_state",
        "reachable": False,
        "version_table_present": False,
        "migrations_current": False,
        "current_heads": [],
        "expected_heads": sorted(expected_migration_heads()),
        "scheme": _scheme(database_url),
    }

    if not database_url:
        report["error_code"] = "DATABASE_URL_missing"
        return report

    engine = None
    try:
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
        with Session(engine) as db:
            db.execute(text("SELECT 1"))
            report["reachable"] = True
            try:
                current = sorted(current_migration_heads(db))
                report["version_table_present"] = True
            except SQLAlchemyError:
                db.rollback()
                current = []
            report["current_heads"] = current
            report["migrations_current"] = set(current) == set(report["expected_heads"])
    except (SQLAlchemyError, RuntimeError, OSError, ValueError):
        report["error_code"] = "database_probe_failed"
    finally:
        if engine is not None:
            engine.dispose()

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-current",
        action="store_true",
        help="Exit non-zero unless the database is reachable and Alembic heads are current.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(os.getenv("DATABASE_URL", "").strip())
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report.get("reachable"):
        return 1
    if args.require_current and not report.get("migrations_current"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
