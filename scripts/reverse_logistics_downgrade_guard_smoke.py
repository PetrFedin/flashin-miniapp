#!/usr/bin/env python3
"""Prove revision 0041 refuses destructive downgrade after physical evidence exists."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import engine

EXPECTED_HEAD = "0041_reverse_logistics_authority"
TARGET = "0040_delivery_authority"
ERROR_FRAGMENT = "0041 downgrade blocked: reverse-logistics evidence exists"


def _config() -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    return config


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("reverse logistics downgrade guard smoke requires PostgreSQL")

    with engine.connect() as connection:
        cases = int(connection.execute(text("SELECT count(*) FROM return_logistics_cases")).scalar_one())
        evidence_movements = int(
            connection.execute(
                text(
                    """
                    SELECT count(*) FROM inventory_movements
                    WHERE kind = 'return'
                      AND source LIKE 'reverse_logistics_event:%'
                    """
                )
            ).scalar_one()
        )
    if cases <= 0:
        raise AssertionError("downgrade guard smoke requires prior physical-return case evidence")
    if evidence_movements <= 0:
        raise AssertionError("downgrade guard smoke requires prior physical-return inventory evidence")

    failure = ""
    try:
        command.downgrade(_config(), TARGET)
    except Exception as exc:  # Alembic deliberately propagates the migration guard.
        failure = str(exc)
    else:
        raise AssertionError("0041 downgrade unexpectedly succeeded with physical-return evidence")

    assert ERROR_FRAGMENT in failure, failure

    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = set(inspect(connection).get_table_names())
        indexes = {
            index["name"]
            for index in inspect(connection).get_indexes("inventory_movements")
        }
        cases_after = int(connection.execute(text("SELECT count(*) FROM return_logistics_cases")).scalar_one())
        evidence_after = int(
            connection.execute(
                text(
                    """
                    SELECT count(*) FROM inventory_movements
                    WHERE kind = 'return'
                      AND source LIKE 'reverse_logistics_event:%'
                    """
                )
            ).scalar_one()
        )

    assert revision == EXPECTED_HEAD, revision
    assert {
        "return_logistics_cases",
        "return_logistics_items",
        "return_logistics_events",
    } <= tables
    assert "uq_inventory_movement_core_kind" in indexes
    assert "uq_inventory_movement_reverse_event_source" in indexes
    assert cases_after == cases
    assert evidence_after == evidence_movements

    print(
        {
            "status": "ok",
            "downgrade_blocked": True,
            "revision_preserved": revision,
            "physical_cases_preserved": cases_after,
            "physical_movements_preserved": evidence_after,
            "transactional_ddl_preserved": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
