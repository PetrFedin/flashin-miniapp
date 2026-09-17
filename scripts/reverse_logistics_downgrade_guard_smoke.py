#!/usr/bin/env python3
"""Prove reverse-logistics authority refuses destructive downgrade with evidence."""

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

EXPECTED_HEAD = "0042_moysklad_stock_evidence_concurrency"
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
        raise AssertionError("reverse-logistics downgrade unexpectedly succeeded with physical evidence")

    assert ERROR_FRAGMENT in failure, failure

    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = set(inspect(connection).get_table_names())
        inventory_indexes = {
            index["name"]
            for index in inspect(connection).get_indexes("inventory_movements")
        }
        conflict_indexes = {
            index["name"]
            for index in inspect(connection).get_indexes("moysklad_conflicts")
        }
        reconciliation_indexes = {
            index["name"]
            for index in inspect(connection).get_indexes("stock_reconciliation_logs")
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

    # PostgreSQL transactional DDL must restore both 0041 physical authority and
    # the 0042 concurrency indexes after 0041 rejects the downgrade chain.
    assert revision == EXPECTED_HEAD, revision
    assert {
        "return_logistics_cases",
        "return_logistics_items",
        "return_logistics_events",
    } <= tables
    assert "uq_inventory_movement_core_kind" in inventory_indexes
    assert "uq_inventory_movement_reverse_event_source" in inventory_indexes
    assert "uq_moysklad_conflict_open_stale_physical_return" in conflict_indexes
    assert "uq_stock_reconciliation_open_blocked_physical_return" in reconciliation_indexes
    assert cases_after == cases
    assert evidence_after == evidence_movements

    print(
        {
            "status": "ok",
            "downgrade_blocked": True,
            "revision_preserved": revision,
            "physical_cases_preserved": cases_after,
            "physical_movements_preserved": evidence_after,
            "concurrency_indexes_preserved": True,
            "transactional_ddl_preserved": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
