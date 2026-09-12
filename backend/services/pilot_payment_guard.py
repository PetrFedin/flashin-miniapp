from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from ..database import SessionLocal
from .pilot_runtime import assert_pilot_new_payment_attempt_allowed


def _verification_failure(message: str, exc: Exception | None = None) -> HTTPException:
    error = HTTPException(
        status_code=503,
        detail={
            "code": "pilot_runtime_integrity_failure",
            "message": message,
        },
    )
    if exc is not None:
        error.__cause__ = exc
    return error


@contextmanager
def pilot_new_payment_attempt_guard(*, order_id: int, settings) -> Iterator[None]:
    """Serialize a fresh external payment create against pilot STOP.

    When pilot enforcement is active this context holds the runtime row lock from
    the final safety check until the caller's YooKassa create finishes. Therefore
    a concurrent STOP either wins before the check (and blocks the create) or
    waits until the already-authorized provider create completes. Existing
    provider fetch/reconcile and refunds intentionally use other paths.
    """

    if not settings.pilot_runtime_enforced:
        yield
        return

    db = SessionLocal()
    try:
        try:
            assert_pilot_new_payment_attempt_allowed(
                db,
                order_id=order_id,
                settings=settings,
            )
        except HTTPException:
            db.rollback()
            raise
        except SQLAlchemyError as exc:
            db.rollback()
            raise _verification_failure(
                "A new payment attempt is unavailable because pilot safety state could not be verified.",
                exc,
            )
        except Exception as exc:
            db.rollback()
            raise _verification_failure(
                "A new payment attempt is unavailable because pilot safety verification failed.",
                exc,
            )

        try:
            yield
        except BaseException:
            db.rollback()
            raise
        else:
            try:
                db.commit()
            except SQLAlchemyError as exc:
                db.rollback()
                raise _verification_failure(
                    "A new payment attempt completed but pilot safety state could not be persisted.",
                    exc,
                )
    finally:
        db.close()


__all__ = ["pilot_new_payment_attempt_guard"]
