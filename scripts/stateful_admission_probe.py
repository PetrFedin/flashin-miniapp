#!/usr/bin/env python3
"""Sanitized PostgreSQL + Redis/Valkey admission probe.

Reads DATABASE_URL and RATE_LIMIT_REDIS_URL from the environment. The probe
never prints connection strings, credentials, hostnames, or raw exception text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.services.database_readiness import (
    current_migration_heads,
    expected_migration_heads,
)
from backend.services.distributed_rate_limit import normalize_redis_url


def _scheme(value: str) -> str:
    candidate = str(value or "").strip()
    if "://" not in candidate:
        return ""
    return candidate.split("://", 1)[0].lower()


def _database_report(database_url: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "reachable": False,
        "migrations_current": False,
        "current_heads": [],
        "expected_heads": sorted(expected_migration_heads()),
        "scheme": _scheme(database_url),
    }
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
            current = sorted(current_migration_heads(db))
            report["current_heads"] = current
            report["migrations_current"] = set(current) == set(report["expected_heads"])
    except (SQLAlchemyError, RuntimeError, OSError, ValueError):
        report["error_code"] = "database_probe_failed"
    finally:
        if engine is not None:
            engine.dispose()
    return report


async def _redis_report(redis_url: str) -> dict[str, Any]:
    normalized = normalize_redis_url(redis_url)
    report: dict[str, Any] = {
        "reachable": False,
        "scheme": _scheme(redis_url),
        "transport_scheme": _scheme(normalized),
    }
    client = Redis.from_url(
        normalized,
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    try:
        report["reachable"] = bool(await client.ping())
    except (OSError, TimeoutError, ValueError):
        report["error_code"] = "rate_limit_store_probe_failed"
    except Exception:
        # Intentionally redact third-party client exception text.
        report["error_code"] = "rate_limit_store_probe_failed"
    finally:
        await client.aclose()
    return report


async def run_probe(*, connectivity_only: bool = False) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    redis_url = os.getenv("RATE_LIMIT_REDIS_URL", "").strip()

    errors: list[str] = []
    if not database_url:
        errors.append("DATABASE_URL_missing")
    if not redis_url:
        errors.append("RATE_LIMIT_REDIS_URL_missing")

    if errors:
        return {
            "schema_version": 1,
            "kind": "flashin_stateful_admission_probe",
            "status": "fail",
            "errors": errors,
        }

    database = _database_report(database_url)
    rate_limit_store = await _redis_report(redis_url)

    database_ok = bool(database.get("reachable"))
    if not connectivity_only:
        database_ok = database_ok and bool(database.get("migrations_current"))

    ok = database_ok and bool(rate_limit_store.get("reachable"))
    return {
        "schema_version": 1,
        "kind": "flashin_stateful_admission_probe",
        "status": "pass" if ok else "fail",
        "mode": "connectivity_only" if connectivity_only else "admission",
        "database": database,
        "rate_limit_store": rate_limit_store,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--connectivity-only",
        action="store_true",
        help="Require PostgreSQL and Redis/Valkey reachability but not current migrations.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = asyncio.run(run_probe(connectivity_only=args.connectivity_only))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
