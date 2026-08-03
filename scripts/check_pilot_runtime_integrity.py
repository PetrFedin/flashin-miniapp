#!/usr/bin/env python3
"""Read-only database integrity audit for the first-20-order pilot runtime."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from backend.database import engine

REQUIRED_TABLES = {
    "customers",
    "orders",
    "pilot_runtime_state",
    "pilot_order_slots",
}

CHECKS: Mapping[str, str] = {
    "invalid_pilot_runtime_singleton": """
        SELECT CASE
            WHEN count(*) = 1 AND min(id) = 1 AND max(id) = 1 THEN 0
            ELSE 1
        END
        FROM pilot_runtime_state
    """,
    "invalid_pilot_runtime_values": """
        SELECT count(*) FROM pilot_runtime_state
        WHERE status NOT IN ('closed', 'active', 'stopped', 'completed')
           OR max_orders < 1
           OR max_orders > 20
           OR accepted_orders < 0
           OR accepted_orders > max_orders
           OR length(admission_sha256) NOT IN (0, 64)
           OR length(release_sha256) NOT IN (0, 64)
    """,
    "invalid_pilot_runtime_transition_state": """
        SELECT count(*) FROM pilot_runtime_state
        WHERE (status = 'closed' AND (accepted_orders <> 0 OR run_id <> ''))
           OR (status = 'active' AND (accepted_orders >= max_orders OR run_id = ''))
           OR (status = 'stopped' AND run_id = '')
           OR (status = 'completed' AND (accepted_orders <> max_orders OR run_id = ''))
    """,
    "pilot_runtime_slot_counter_mismatch": """
        SELECT count(*) FROM pilot_runtime_state state
        WHERE state.accepted_orders <> (
            SELECT count(*) FROM pilot_order_slots slot
            WHERE slot.run_id = state.run_id
        )
    """,
    "invalid_pilot_order_slot_sequence": """
        SELECT count(*) FROM pilot_order_slots
        WHERE sequence < 1 OR sequence > 20
    """,
    "orphan_or_mismatched_pilot_order_slots": """
        SELECT count(*)
        FROM pilot_order_slots slot
        LEFT JOIN orders customer_order ON customer_order.id = slot.order_id
        LEFT JOIN customers customer ON customer.id = slot.customer_id
        WHERE customer_order.id IS NULL
           OR customer.id IS NULL
           OR customer_order.customer_id <> slot.customer_id
    """,
    "pilot_slots_outside_current_run": """
        SELECT count(*)
        FROM pilot_order_slots slot
        WHERE NOT EXISTS (
            SELECT 1 FROM pilot_runtime_state state
            WHERE state.run_id = slot.run_id
        )
    """,
}


class MissingPilotRuntimeSchema(RuntimeError):
    def __init__(self, missing_tables: set[str]):
        self.missing_tables = missing_tables
        super().__init__("missing tables: " + ", ".join(sorted(missing_tables)))


def run_audit(connection: Connection) -> dict[str, int]:
    present_tables = set(inspect(connection).get_table_names())
    missing = REQUIRED_TABLES - present_tables
    if missing:
        raise MissingPilotRuntimeSchema(missing)
    return {
        name: int(connection.execute(text(query)).scalar_one())
        for name, query in CHECKS.items()
    }


def main() -> int:
    try:
        with engine.connect() as connection:
            results = run_audit(connection)
    except MissingPilotRuntimeSchema as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "missing_pilot_runtime_schema",
                    "missing_tables": sorted(exc.missing_tables),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except SQLAlchemyError as exc:
        print(
            json.dumps(
                {"ok": False, "reason": exc.__class__.__name__},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    violations = {name: count for name, count in results.items() if count > 0}
    print(
        json.dumps(
            {"ok": not violations, "checks": results, "violations": violations},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
