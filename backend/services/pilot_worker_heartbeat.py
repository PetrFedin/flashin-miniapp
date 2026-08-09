from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..database import SessionLocal, utcnow_naive
from ..pilot_models import PilotWorkerHeartbeat

SCHEDULER_WORKER = "scheduler"
NOTIFICATION_WORKER = "notification_worker"
REQUIRED_WORKER_STALE_SECONDS = {
    SCHEDULER_WORKER: 90,
    NOTIFICATION_WORKER: 45,
}


def _worker_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in REQUIRED_WORKER_STALE_SECONDS:
        raise ValueError("Unsupported pilot worker heartbeat name")
    return normalized


def touch_worker_heartbeat(
    db: Session,
    worker_name: str,
    *,
    now: datetime | None = None,
) -> datetime:
    """Upsert one logical worker heartbeat in the caller transaction."""
    normalized = _worker_name(worker_name)
    seen_at = now or utcnow_naive()
    row = db.get(PilotWorkerHeartbeat, normalized)
    if row is None:
        db.add(
            PilotWorkerHeartbeat(
                worker_name=normalized,
                last_seen_at=seen_at,
            )
        )
    else:
        row.last_seen_at = seen_at
    db.flush()
    return seen_at


def record_worker_heartbeat(
    worker_name: str,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    now: datetime | None = None,
) -> datetime:
    """Persist a heartbeat in its own short transaction.

    The scheduler and Telegram notification worker call this from their process
    loops. A heartbeat write never shares a transaction with money/order work.
    """
    db = session_factory()
    try:
        seen_at = touch_worker_heartbeat(db, worker_name, now=now)
        db.commit()
        return seen_at
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def build_required_worker_liveness(
    db: Session,
    *,
    current_run_opened_at: datetime,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate logical pilot worker health without exposing instance details."""
    effective_now = now or utcnow_naive()
    if current_run_opened_at > effective_now:
        raise ValueError("Pilot worker liveness scope cannot start in the future")

    rows = {
        row.worker_name: row
        for row in db.query(PilotWorkerHeartbeat)
        .filter(PilotWorkerHeartbeat.worker_name.in_(tuple(REQUIRED_WORKER_STALE_SECONDS)))
        .all()
    }
    workers: dict[str, dict[str, Any]] = {}
    blocking_codes: list[str] = []

    for worker_name, stale_seconds in REQUIRED_WORKER_STALE_SECONDS.items():
        row = rows.get(worker_name)
        if row is None:
            blocking_codes.append(f"{worker_name}_heartbeat_missing")
            workers[worker_name] = {
                "healthy": False,
                "fresh": False,
                "seen_in_current_run": False,
                "age_seconds": None,
                "stale_after_seconds": stale_seconds,
            }
            continue

        age_seconds = max(0.0, (effective_now - row.last_seen_at).total_seconds())
        seen_in_current_run = row.last_seen_at >= current_run_opened_at
        fresh = age_seconds <= stale_seconds
        healthy = bool(seen_in_current_run and fresh)
        if not seen_in_current_run:
            blocking_codes.append(f"{worker_name}_heartbeat_precedes_current_run")
        elif not fresh:
            blocking_codes.append(f"{worker_name}_heartbeat_stale")

        workers[worker_name] = {
            "healthy": healthy,
            "fresh": fresh,
            "seen_in_current_run": seen_in_current_run,
            "age_seconds": age_seconds,
            "stale_after_seconds": stale_seconds,
        }

    return {
        "healthy": not blocking_codes,
        "blocking_codes": blocking_codes,
        "workers": workers,
    }
