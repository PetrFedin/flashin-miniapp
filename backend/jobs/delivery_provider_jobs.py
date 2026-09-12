from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from ..services.delivery_provider_runtime import (
    DELIVERY_BOOKING_COMMAND,
    DELIVERY_COMMAND_PROVIDER,
)
from ..services.provider_commands import (
    claim_provider_commands,
    fail_provider_command,
    finish_provider_command,
)

DeliveryBookingAdapter = Callable[[dict[str, Any]], Awaitable[str] | str]


class DeliveryBookingReviewRequired(RuntimeError):
    """A booking outcome must be reconciled by an operator, not retried blindly."""


async def _book(
    payload: dict[str, Any],
    *,
    live_adapter: DeliveryBookingAdapter | None,
) -> str:
    mode = str(payload.get("provider_mode") or "").strip().lower()
    shipment_id = int(payload["shipment_id"])
    quote_id = int(payload["quote_id"])

    if mode == "sandbox":
        # Deliberately obvious non-live evidence. This never represents a real
        # carrier acceptance and cannot be confused with a production booking.
        return f"sandbox-delivery-{shipment_id}-{quote_id}"

    if mode != "live":
        raise DeliveryBookingReviewRequired(
            f"Delivery booking command has invalid mode {mode!r}"
        )
    if live_adapter is None:
        raise DeliveryBookingReviewRequired(
            "Live delivery adapter is not configured; booking was not attempted"
        )

    try:
        result = live_adapter(payload)
        if inspect.isawaitable(result):
            result = await result
    except TimeoutError as exc:
        # A carrier may have accepted the request before the connection timed
        # out. Blind retry could create a duplicate shipment, so stop for
        # reconciliation rather than claiming failure or success.
        raise DeliveryBookingReviewRequired(
            "Live delivery booking timed out with an ambiguous provider outcome"
        ) from exc

    external_id = str(result or "").strip()
    if not external_id:
        raise DeliveryBookingReviewRequired(
            "Live delivery adapter returned no booking identifier"
        )
    return external_id


async def process_delivery_provider_commands(
    db: Session,
    limit: int = 50,
    *,
    live_adapter: DeliveryBookingAdapter | None = None,
) -> dict[str, int]:
    claimed = claim_provider_commands(
        db,
        provider=DELIVERY_COMMAND_PROVIDER,
        limit=limit,
    )
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
                raise DeliveryBookingReviewRequired(
                    "Delivery booking command payload is not an object"
                )
            if str(command["command_type"]) != DELIVERY_BOOKING_COMMAND:
                raise DeliveryBookingReviewRequired(
                    f"Unsupported delivery provider command: {command['command_type']}"
                )
            external_id = await _book(payload, live_adapter=live_adapter)
            if finish_provider_command(
                db,
                command_id,
                lease_token,
                external_id=external_id,
            ):
                result["sent"] += 1
            else:
                result["ignored"] += 1
        except DeliveryBookingReviewRequired as exc:
            state = fail_provider_command(
                db,
                command_id,
                lease_token,
                exc,
                review_required=True,
            )
            result[state] = result.get(state, 0) + 1
        except Exception as exc:
            state = fail_provider_command(
                db,
                command_id,
                lease_token,
                exc,
            )
            result[state] = result.get(state, 0) + 1

    return result
