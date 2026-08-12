from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from ..database import SessionLocal
from .pilot_runtime import assert_pilot_new_payment_attempt_allowed


def guard_pilot_new_payment_attempt(*, order_id: int, settings) -> None:
    """Re-check pilot safety in an independent transaction before YooKassa create.

    The caller invokes this only for a *fresh* provider payment creation. Existing
    provider attempts are fetched/reconciled through a different path, and refund
    operations intentionally remain available so a stopped pilot can unwind money.
    """

    if not settings.pilot_runtime_enforced:
        return

    db = SessionLocal()
    try:
        assert_pilot_new_payment_attempt_allowed(
            db,
            order_id=order_id,
            settings=settings,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "pilot_runtime_integrity_failure",
                "message": "A new payment attempt is unavailable because pilot safety state could not be verified.",
            },
        ) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "pilot_runtime_integrity_failure",
                "message": "A new payment attempt is unavailable because pilot safety verification failed.",
            },
        ) from exc
    finally:
        db.close()


__all__ = ["guard_pilot_new_payment_attempt"]
