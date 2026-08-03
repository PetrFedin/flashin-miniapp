"""PostgreSQL-backed ownership for scheduled jobs.

APScheduler's ``max_instances`` protects only one Python process. Session-level
advisory locks keep a job single-owner across scheduler containers, one-shot
worker entrypoints, and manual operations while unrelated jobs remain parallel.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..database import SessionLocal, engine


T = TypeVar("T")
_JOB_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_JOB_NAME_MAX_LENGTH = 120
_LOCK_NAMESPACE = "flashin:scheduler"


def normalize_job_name(job_name: str) -> str:
    normalized = str(job_name or "").strip()
    if (
        not normalized
        or len(normalized) > _JOB_NAME_MAX_LENGTH
        or not _JOB_NAME_RE.fullmatch(normalized)
    ):
        raise ValueError("Scheduler job name is invalid")
    return normalized


def advisory_lock_key(job_name: str) -> int:
    normalized = normalize_job_name(job_name)
    digest = hashlib.sha256(
        f"{_LOCK_NAMESPACE}:{normalized}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def run_with_scheduler_lock(
    job_name: str,
    callback: Callable[[], T],
    *,
    database_engine: Engine = engine,
) -> dict[str, Any]:
    """Run ``callback`` only when this process owns the named advisory lock.

    The lock lives on a dedicated checked-out connection. It is explicitly
    released before that connection returns to the pool. PostgreSQL also
    releases it automatically if the connection is lost.
    """
    if not callable(callback):
        raise TypeError("Scheduler callback must be callable")

    normalized = normalize_job_name(job_name)
    lock_key = advisory_lock_key(normalized)

    with database_engine.connect() as connection:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            ).scalar_one()
        )
        connection.commit()
        if not acquired:
            return {
                "status": "skipped",
                "reason": "lock_busy",
                "job": normalized,
            }

        try:
            result = callback()
            return {
                "status": "executed",
                "job": normalized,
                "result": result,
            }
        finally:
            released = bool(
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                ).scalar_one()
            )
            connection.commit()
            if not released:
                connection.invalidate()


def _call_with_db(
    callback: Callable[[Session], T],
    *,
    session_factory: Callable[[], Session] = SessionLocal,
) -> T:
    db = session_factory()
    try:
        return callback(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _call_with_async_db(
    callback: Callable[[Session], Awaitable[T]],
    *,
    session_factory: Callable[[], Session] = SessionLocal,
) -> T:
    db = session_factory()
    try:
        return asyncio.run(callback(db))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_locked_db_job(
    job_name: str,
    callback: Callable[[Session], T],
    *,
    database_engine: Engine = engine,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, Any]:
    return run_with_scheduler_lock(
        job_name,
        lambda: _call_with_db(callback, session_factory=session_factory),
        database_engine=database_engine,
    )


def run_locked_async_db_job(
    job_name: str,
    callback: Callable[[Session], Awaitable[T]],
    *,
    database_engine: Engine = engine,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, Any]:
    return run_with_scheduler_lock(
        job_name,
        lambda: _call_with_async_db(callback, session_factory=session_factory),
        database_engine=database_engine,
    )
