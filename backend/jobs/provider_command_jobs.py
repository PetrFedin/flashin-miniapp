from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from ..services.moysklad_outbound import (
    MoySkladReviewRequired,
    export_customer_order,
    export_demand,
    export_sales_return,
)
from ..services.provider_command_safety import (
    enforce_terminal_provider_command_pilot_stop,
)
from ..services.provider_commands import (
    claim_provider_commands,
    fail_provider_command,
    finish_provider_command,
)

_Handler = Callable[[Session, dict[str, Any]], Awaitable[str]]
_TERMINAL_FAILURE_STATES = {"failed", "review_required"}


async def _customer_order(db: Session, payload: dict[str, Any]) -> str:
    return await export_customer_order(db, int(payload["order_id"]))


async def _demand(db: Session, payload: dict[str, Any]) -> str:
    return await export_demand(db, int(payload["order_id"]))


async def _sales_return(db: Session, payload: dict[str, Any]) -> str:
    return await export_sales_return(
        db,
        int(payload["order_id"]),
        int(payload["return_id"]),
    )


_HANDLERS: dict[str, _Handler] = {
    "moysklad.customer_order.create": _customer_order,
    "moysklad.demand.create": _demand,
    "moysklad.sales_return.create": _sales_return,
}


async def process_provider_commands(db: Session, limit: int = 50) -> dict[str, int]:
    # Recover a missed pilot stop before taking more work. This also covers the
    # case where a prior worker process persisted a terminal command and died
    # before it could persist the circuit-breaker transition.
    enforce_terminal_provider_command_pilot_stop(db)

    claimed = claim_provider_commands(db, provider="moysklad", limit=limit)
    result = {
        "claimed": len(claimed),
        "sent": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "review_required": 0,
        "ignored": 0,
    }

    for command in claimed:
        command_id = int(command["id"])
        lease_token = str(command["lease_token"])
        try:
            payload = json.loads(str(command["payload_json"]))
            if not isinstance(payload, dict):
                raise MoySkladReviewRequired("Provider command payload is not an object")
            handler = _HANDLERS.get(str(command["command_type"]))
            if handler is None:
                raise MoySkladReviewRequired(
                    f"Unsupported provider command type: {command['command_type']}"
                )
            external_id = await handler(db, payload)
            if finish_provider_command(
                db,
                command_id,
                lease_token,
                external_id=external_id,
            ):
                result["sent"] += 1
            else:
                result["ignored"] += 1
        except MoySkladReviewRequired as exc:
            state = fail_provider_command(
                db,
                command_id,
                lease_token,
                exc,
                review_required=True,
            )
            result[state] = result.get(state, 0) + 1
            if state in _TERMINAL_FAILURE_STATES:
                enforce_terminal_provider_command_pilot_stop(db)
        except Exception as exc:
            state = fail_provider_command(
                db,
                command_id,
                lease_token,
                exc,
            )
            result[state] = result.get(state, 0) + 1
            if state in _TERMINAL_FAILURE_STATES:
                enforce_terminal_provider_command_pilot_stop(db)

    return result
