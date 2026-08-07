from __future__ import annotations

from sqlalchemy.orm import Session

from ..pilot_models import PilotOrderSlot, PilotRuntimeState
from ..provider_models import ProviderCommand
from .pilot_circuit_breaker import stop_pilot_for_order

_PILOT_CRITICAL_PROVIDERS = ("moysklad",)
_TERMINAL_PROVIDER_STATUSES = ("review_required", "failed")
_STOP_REASONS = {
    "review_required": "provider_command_review_required",
    "failed": "provider_command_terminal_failed",
}
_RUNTIME_STATUSES_REQUIRING_PROTECTION = {"active", "completed"}


def _empty_result(*, current_run: bool = False) -> dict[str, int]:
    return {
        "current_run": int(current_run),
        "terminal_commands": 0,
        "affected_orders": 0,
        "stopped": 0,
    }


def enforce_terminal_provider_command_pilot_stop(db: Session) -> dict[str, int]:
    """Stop the current pilot when a pilot-bound MoySklad command is terminal.

    The coupling is deliberately narrow: only terminal commands for orders admitted
    to the *current* pilot run can stop checkout. Historical/non-pilot commands and
    retryable pending/processing commands are ignored.
    """

    state = (
        db.query(PilotRuntimeState)
        .filter(PilotRuntimeState.id == 1)
        .first()
    )
    if (
        state is None
        or not state.run_id
        or state.status not in _RUNTIME_STATUSES_REQUIRING_PROTECTION
    ):
        return _empty_result()

    order_rows = (
        db.query(PilotOrderSlot.order_id)
        .filter(PilotOrderSlot.run_id == state.run_id)
        .order_by(PilotOrderSlot.sequence.asc())
        .all()
    )
    order_ids = [str(int(row[0])) for row in order_rows]
    if not order_ids:
        return _empty_result(current_run=True)

    terminal = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider.in_(_PILOT_CRITICAL_PROVIDERS),
            ProviderCommand.aggregate_type == "order",
            ProviderCommand.aggregate_id.in_(order_ids),
            ProviderCommand.status.in_(_TERMINAL_PROVIDER_STATUSES),
        )
        .order_by(ProviderCommand.id.asc())
        .all()
    )
    if not terminal:
        return _empty_result(current_run=True)

    affected_orders = len({row.aggregate_id for row in terminal})
    trigger = terminal[0]
    trip = stop_pilot_for_order(
        db,
        order_id=int(trigger.aggregate_id),
        reason=_STOP_REASONS[trigger.status],
    )
    # stop_pilot_for_order intentionally participates in the caller transaction.
    # This reconciliation is the durability boundary for the worker, so persist
    # the stop before any further provider command is claimed.
    db.commit()

    return {
        "current_run": 1,
        "terminal_commands": len(terminal),
        "affected_orders": affected_orders,
        "stopped": int(trip.changed),
    }
