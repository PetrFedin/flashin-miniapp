import hashlib
import json
import os
import socket
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable, TypeVar
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ..database import SessionLocal, engine
from ..job_models import (
    MAX_JOB_DURATION_MS,
    MAX_JOB_ERROR_LENGTH,
    MAX_JOB_RESULT_LENGTH,
    ScheduledJobRun,
)

T = TypeVar("T")
_LOCAL_LOCKS: dict[int, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()
_STALE_RUN_ERROR = "Recovered stale scheduled job run after distributed lock acquisition"


@dataclass(frozen=True)
class JobExecutionOutcome:
    job_name: str
    status: str
    run_id: int
    result: Any = None


def _normalized_job_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized or len(normalized) > 120:
        raise ValueError("Scheduled job name is invalid")
    return normalized


def _lock_key(job_name: str) -> int:
    digest = hashlib.blake2b(job_name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:255]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, (datetime, date)):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            else:
                value = value.astimezone(UTC)
            return value.isoformat(timespec="seconds").replace("+00:00", "Z")
        return value.isoformat()
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "id"):
        return {
            "type": value.__class__.__name__,
            "id": getattr(value, "id", None),
            "status": getattr(value, "status", None),
        }
    return str(value)


def _result_json(value: Any) -> str:
    serialized = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(serialized) <= MAX_JOB_RESULT_LENGTH:
        return serialized
    return json.dumps(
        {
            "truncated": True,
            "preview": serialized[: MAX_JOB_RESULT_LENGTH - 64],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )[:MAX_JOB_RESULT_LENGTH]


class DistributedJobLock(AbstractContextManager):
    def __init__(self, job_name: str):
        self.job_name = _normalized_job_name(job_name)
        self.key = _lock_key(self.job_name)
        self.acquired = False
        self._connection = None
        self._local_lock: threading.Lock | None = None

    def __enter__(self):
        if engine.dialect.name == "postgresql":
            self._connection = engine.connect()
            try:
                self.acquired = bool(
                    self._connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": self.key},
                    ).scalar()
                )
                self._connection.commit()
            except Exception:
                self._connection.close()
                self._connection = None
                raise
            return self

        with _LOCAL_LOCKS_GUARD:
            self._local_lock = _LOCAL_LOCKS.setdefault(self.key, threading.Lock())
        self.acquired = self._local_lock.acquire(blocking=False)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._connection is not None:
            try:
                if self.acquired:
                    self._connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": self.key},
                    )
                    self._connection.commit()
            finally:
                self._connection.close()
                self._connection = None
        elif self.acquired and self._local_lock is not None:
            self._local_lock.release()
        self.acquired = False
        return False


def _create_run(
    session_factory: sessionmaker,
    *,
    job_name: str,
    trigger: str,
    status: str,
    result: Any = None,
) -> ScheduledJobRun:
    now = datetime.utcnow()
    run = ScheduledJobRun(
        job_name=job_name,
        run_token=uuid4().hex,
        trigger=trigger,
        status=status,
        worker_id=_worker_id(),
        started_at=now,
        finished_at=now if status == "skipped" else None,
        duration_ms=0 if status == "skipped" else None,
        result_json=_result_json(result),
        error="",
    )
    db = session_factory()
    try:
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _recover_stale_runs(session_factory: sessionmaker, job_name: str) -> int:
    """Close abandoned `running` audits once the same job lock is acquired.

    Holding the distributed lock proves there is no live guarded execution with
    this job name, so any surviving running audit belongs to a terminated or
    pre-lock process and can be marked failed safely.
    """

    now = datetime.utcnow()
    db = session_factory()
    try:
        rows = (
            db.query(ScheduledJobRun)
            .filter(
                ScheduledJobRun.job_name == job_name,
                ScheduledJobRun.status == "running",
            )
            .order_by(ScheduledJobRun.started_at.asc(), ScheduledJobRun.id.asc())
            .with_for_update()
            .all()
        )
        for run in rows:
            elapsed_ms = int(max((now - run.started_at).total_seconds() * 1000, 0))
            run.status = "failed"
            run.finished_at = now
            run.duration_ms = min(elapsed_ms, MAX_JOB_DURATION_MS)
            run.error = _STALE_RUN_ERROR
        db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _finish_run(
    session_factory: sessionmaker,
    run_id: int,
    *,
    status: str,
    started_monotonic: float,
    result: Any = None,
    error: str = "",
) -> None:
    duration_ms = min(
        max(int((time.monotonic() - started_monotonic) * 1000), 0),
        MAX_JOB_DURATION_MS,
    )
    db = session_factory()
    try:
        run = (
            db.query(ScheduledJobRun)
            .filter(ScheduledJobRun.id == run_id, ScheduledJobRun.status == "running")
            .with_for_update()
            .first()
        )
        if not run:
            db.rollback()
            return
        run.status = status
        run.finished_at = datetime.utcnow()
        run.duration_ms = duration_ms
        run.result_json = _result_json(result)
        run.error = str(error or "").strip()[:MAX_JOB_ERROR_LENGTH]
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_sync_job(
    job_name: str,
    function: Callable[[Session], T],
    *,
    trigger: str = "scheduler",
    session_factory: sessionmaker = SessionLocal,
) -> JobExecutionOutcome:
    normalized_name = _normalized_job_name(job_name)
    with DistributedJobLock(normalized_name) as lock:
        if not lock.acquired:
            skipped = _create_run(
                session_factory,
                job_name=normalized_name,
                trigger=trigger,
                status="skipped",
                result={"reason": "distributed_lock_unavailable"},
            )
            return JobExecutionOutcome(normalized_name, "skipped", skipped.id)

        _recover_stale_runs(session_factory, normalized_name)
        run = _create_run(
            session_factory,
            job_name=normalized_name,
            trigger=trigger,
            status="running",
        )
        started_monotonic = time.monotonic()
        work_db = session_factory()
        try:
            result = function(work_db)
            work_db.commit()
            _finish_run(
                session_factory,
                run.id,
                status="succeeded",
                started_monotonic=started_monotonic,
                result=result,
            )
            return JobExecutionOutcome(normalized_name, "succeeded", run.id, result)
        except BaseException as exc:
            work_db.rollback()
            _finish_run(
                session_factory,
                run.id,
                status="failed",
                started_monotonic=started_monotonic,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            raise
        finally:
            work_db.close()


async def run_async_job(
    job_name: str,
    function: Callable[[Session], Awaitable[T]],
    *,
    trigger: str = "scheduler",
    session_factory: sessionmaker = SessionLocal,
) -> JobExecutionOutcome:
    normalized_name = _normalized_job_name(job_name)
    with DistributedJobLock(normalized_name) as lock:
        if not lock.acquired:
            skipped = _create_run(
                session_factory,
                job_name=normalized_name,
                trigger=trigger,
                status="skipped",
                result={"reason": "distributed_lock_unavailable"},
            )
            return JobExecutionOutcome(normalized_name, "skipped", skipped.id)

        _recover_stale_runs(session_factory, normalized_name)
        run = _create_run(
            session_factory,
            job_name=normalized_name,
            trigger=trigger,
            status="running",
        )
        started_monotonic = time.monotonic()
        work_db = session_factory()
        try:
            result = await function(work_db)
            work_db.commit()
            _finish_run(
                session_factory,
                run.id,
                status="succeeded",
                started_monotonic=started_monotonic,
                result=result,
            )
            return JobExecutionOutcome(normalized_name, "succeeded", run.id, result)
        except BaseException as exc:
            work_db.rollback()
            _finish_run(
                session_factory,
                run.id,
                status="failed",
                started_monotonic=started_monotonic,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            raise
        finally:
            work_db.close()
