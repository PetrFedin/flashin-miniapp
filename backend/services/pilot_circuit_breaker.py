from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import SessionLocal, utcnow_naive
from ..pilot_models import PilotOrderSlot, PilotRuntimeState


@dataclass(frozen=True)
class PilotCircuitTrip:
    pilot_order: bool
    changed: bool
    status: str | None


class PilotCircuitBreakerError(RuntimeError):
    pass


def _normalize_reason(reason: str) -> str:
    normalized = "_".join(str(reason or "automatic_integrity_stop").strip().lower().split())
    safe = "".join(character for character in normalized if character.isalnum() or character in "_-:.")
    return (safe or "automatic_integrity_stop")[:160]


def stop_pilot_for_order(
    db: Session,
    *,
    order_id: int,
    reason: str,
) -> PilotCircuitTrip:
    slot = (
        db.query(PilotOrderSlot)
        .filter(PilotOrderSlot.order_id == order_id)
        .with_for_update()
        .first()
    )
    if slot is None:
        return PilotCircuitTrip(pilot_order=False, changed=False, status=None)

    state = (
        db.query(PilotRuntimeState)
        .filter(PilotRuntimeState.id == 1)
        .with_for_update()
        .first()
    )
    if state is None:
        raise PilotCircuitBreakerError("Pilot runtime state is missing for a pilot order")

    normalized_reason = _normalize_reason(reason)
    if state.run_id != slot.run_id:
        normalized_reason = "pilot_slot_runtime_mismatch"

    changed = state.status in {"active", "completed"}
    if changed:
        state.status = "stopped"
        state.stopped_at = utcnow_naive()
        state.stop_reason = f"auto:{normalized_reason}"
        state.updated_at = utcnow_naive()

    return PilotCircuitTrip(
        pilot_order=True,
        changed=changed,
        status=state.status,
    )


def trip_pilot_circuit_breaker(*, order_id: int, reason: str) -> PilotCircuitTrip:
    db = SessionLocal()
    try:
        result = stop_pilot_for_order(db, order_id=order_id, reason=reason)
        db.commit()
        return result
    except PilotCircuitBreakerError:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise PilotCircuitBreakerError(
            "Unable to persist the pilot circuit-breaker state"
        ) from exc
    finally:
        db.close()
