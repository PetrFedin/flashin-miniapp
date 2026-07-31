from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

JOB_RUN_STATUSES = frozenset({"running", "succeeded", "failed", "skipped"})
JOB_RUN_TRIGGERS = frozenset({"scheduler", "worker", "manual", "api", "test"})
MAX_JOB_NAME_LENGTH = 120
MAX_JOB_RESULT_LENGTH = 16_384
MAX_JOB_ERROR_LENGTH = 2_000
MAX_JOB_WORKER_LENGTH = 255
MAX_JOB_DURATION_MS = 86_400_000


class ScheduledJobRun(Base):
    __tablename__ = "scheduled_job_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('failed', 'running', 'skipped', 'succeeded')",
            name="ck_scheduled_job_runs_status_valid",
        ),
        CheckConstraint(
            "trigger IN ('api', 'manual', 'scheduler', 'test', 'worker')",
            name="ck_scheduled_job_runs_trigger_valid",
        ),
        CheckConstraint(
            f"length(job_name) BETWEEN 1 AND {MAX_JOB_NAME_LENGTH}",
            name="ck_scheduled_job_runs_job_name_size",
        ),
        CheckConstraint(
            "job_name = lower(trim(job_name))",
            name="ck_scheduled_job_runs_job_name_normalized",
        ),
        CheckConstraint(
            "length(run_token) = 32",
            name="ck_scheduled_job_runs_token_size",
        ),
        CheckConstraint(
            f"length(worker_id) BETWEEN 1 AND {MAX_JOB_WORKER_LENGTH}",
            name="ck_scheduled_job_runs_worker_size",
        ),
        CheckConstraint(
            f"length(result_json) <= {MAX_JOB_RESULT_LENGTH}",
            name="ck_scheduled_job_runs_result_size",
        ),
        CheckConstraint(
            f"length(error) <= {MAX_JOB_ERROR_LENGTH}",
            name="ck_scheduled_job_runs_error_size",
        ),
        CheckConstraint(
            f"duration_ms IS NULL OR duration_ms BETWEEN 0 AND {MAX_JOB_DURATION_MS}",
            name="ck_scheduled_job_runs_duration_range",
        ),
        CheckConstraint(
            "((status = 'running' AND finished_at IS NULL AND duration_ms IS NULL AND error = '') "
            "OR (status IN ('succeeded', 'skipped') AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL AND error = '') "
            "OR (status = 'failed' AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL AND length(trim(error)) > 0))",
            name="ck_scheduled_job_runs_state_coherent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(MAX_JOB_NAME_LENGTH), index=True)
    run_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    worker_id: Mapped[str] = mapped_column(String(MAX_JOB_WORKER_LENGTH))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")


def _validate_job_run(_mapper, _connection, target: ScheduledJobRun) -> None:
    target.job_name = str(target.job_name or "").strip().lower()
    target.run_token = str(target.run_token or "").strip().lower()
    target.trigger = str(target.trigger or "").strip().lower()
    target.status = str(target.status or "").strip().lower()
    target.worker_id = str(target.worker_id or "").strip()
    target.result_json = str(target.result_json or "{}")[:MAX_JOB_RESULT_LENGTH]
    target.error = str(target.error or "").strip()[:MAX_JOB_ERROR_LENGTH]

    if not target.job_name or len(target.job_name) > MAX_JOB_NAME_LENGTH:
        raise HTTPException(status_code=400, detail="Scheduled job name is invalid")
    if len(target.run_token) != 32:
        raise HTTPException(status_code=400, detail="Scheduled job run token is invalid")
    if target.trigger not in JOB_RUN_TRIGGERS:
        raise HTTPException(status_code=400, detail="Scheduled job trigger is invalid")
    if target.status not in JOB_RUN_STATUSES:
        raise HTTPException(status_code=400, detail="Scheduled job status is invalid")
    if not target.worker_id or len(target.worker_id) > MAX_JOB_WORKER_LENGTH:
        raise HTTPException(status_code=400, detail="Scheduled job worker id is invalid")

    if target.duration_ms is not None:
        try:
            target.duration_ms = int(target.duration_ms)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Scheduled job duration is invalid") from exc
        if target.duration_ms < 0 or target.duration_ms > MAX_JOB_DURATION_MS:
            raise HTTPException(status_code=400, detail="Scheduled job duration is out of range")

    if target.status == "running":
        if target.finished_at is not None or target.duration_ms is not None or target.error:
            raise HTTPException(status_code=400, detail="Running scheduled job state is inconsistent")
    elif target.finished_at is None or target.duration_ms is None:
        raise HTTPException(status_code=400, detail="Terminal scheduled job requires completion data")
    elif target.status == "failed" and not target.error:
        raise HTTPException(status_code=400, detail="Failed scheduled job requires an error")
    elif target.status in {"succeeded", "skipped"} and target.error:
        raise HTTPException(status_code=400, detail="Successful or skipped job cannot contain an error")


for _event_name in ("before_insert", "before_update"):
    if not event.contains(ScheduledJobRun, _event_name, _validate_job_run):
        event.listen(ScheduledJobRun, _event_name, _validate_job_run)
