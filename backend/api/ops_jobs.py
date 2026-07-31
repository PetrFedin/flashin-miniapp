from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..job_models import JOB_RUN_STATUSES, JOB_RUN_TRIGGERS, ScheduledJobRun
from ..jobs.execution import JobExecutionOutcome, run_async_job, run_sync_job
from ..jobs.registry import JobDefinition, get_job_definition, list_job_definitions
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["ops-jobs"])
JOB_SESSION_FACTORY = SessionLocal
_HISTORY_PERMISSION = "security.read"
_MAX_PAGE = 100_000
_MAX_LIMIT = 100


def _safe_result(value: str) -> Any:
    raw = str(value or "{}").strip()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"unavailable": True, "preview": raw[:500]}
    if isinstance(parsed, (dict, list, str, int, float, bool)) or parsed is None:
        return parsed
    return {"unavailable": True}


def _definition_payload(definition: JobDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "title": definition.title,
        "description": definition.description,
        "kind": definition.kind,
        "permission": definition.permission,
        "manual_enabled": definition.manual_enabled,
        "retry_enabled": definition.retry_enabled,
    }


def _run_payload(run: ScheduledJobRun, *, include_result: bool = True) -> dict[str, Any]:
    payload = {
        "id": run.id,
        "job_name": run.job_name,
        "run_token": run.run_token,
        "trigger": run.trigger,
        "status": run.status,
        "worker_id": run.worker_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": run.duration_ms,
        "error": run.error,
    }
    if include_result:
        payload["result"] = _safe_result(run.result_json)
    return payload


def _require_definition(job_name: str) -> JobDefinition:
    definition = get_job_definition(job_name)
    if definition is None:
        raise HTTPException(status_code=404, detail="Scheduled job is not registered")
    return definition


def _latest_failed_manual_run(db: Session, job_name: str, started_at: datetime):
    return (
        db.query(ScheduledJobRun)
        .filter(
            ScheduledJobRun.job_name == job_name,
            ScheduledJobRun.trigger == "manual",
            ScheduledJobRun.status == "failed",
            ScheduledJobRun.started_at >= started_at,
        )
        .order_by(ScheduledJobRun.id.desc())
        .first()
    )


def _execute_definition(definition: JobDefinition) -> JobExecutionOutcome:
    if definition.kind == "async":
        return asyncio.run(
            run_async_job(
                definition.name,
                definition.function,
                trigger="manual",
                session_factory=JOB_SESSION_FACTORY,
            )
        )
    return run_sync_job(
        definition.name,
        definition.function,
        trigger="manual",
        session_factory=JOB_SESSION_FACTORY,
    )


def _execute_and_audit(
    *,
    definition: JobDefinition,
    admin,
    audit_db: Session,
    source_run_id: int | None = None,
) -> dict[str, Any]:
    started_at = datetime.utcnow()
    try:
        outcome = _execute_definition(definition)
    except Exception:
        audit_db.rollback()
        failed = _latest_failed_manual_run(audit_db, definition.name, started_at)
        log_admin_action(
            audit_db,
            admin,
            "scheduled_job.failed",
            entity_type="scheduled_job_run",
            entity_id=failed.id if failed else "",
            payload={
                "job_name": definition.name,
                "source_run_id": source_run_id,
            },
        )
        audit_db.commit()
        logger.exception("Manual scheduled job failed: %s", definition.name)
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Scheduled job failed",
                "job_name": definition.name,
                "run_id": failed.id if failed else None,
            },
        ) from None

    audit_db.rollback()
    run = audit_db.query(ScheduledJobRun).filter(ScheduledJobRun.id == outcome.run_id).first()
    if run is None:
        raise HTTPException(status_code=500, detail="Scheduled job audit record is missing")

    log_admin_action(
        audit_db,
        admin,
        "scheduled_job.executed",
        entity_type="scheduled_job_run",
        entity_id=run.id,
        payload={
            "job_name": definition.name,
            "status": run.status,
            "source_run_id": source_run_id,
        },
    )
    audit_db.commit()

    if outcome.status == "skipped":
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Scheduled job is already running",
                "job_name": definition.name,
                "run_id": run.id,
            },
        )
    return _run_payload(run)


@router.get("/definitions")
def definitions(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, _HISTORY_PERMISSION)
    return [_definition_payload(item) for item in list_job_definitions()]


@router.get("/runs")
def list_runs(
    job_name: str | None = None,
    status: str | None = None,
    trigger: str | None = None,
    page: int = Query(default=1, ge=1, le=_MAX_PAGE),
    limit: int = Query(default=25, ge=1, le=_MAX_LIMIT),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, _HISTORY_PERMISSION)
    query = db.query(ScheduledJobRun)

    if job_name:
        definition = _require_definition(job_name)
        query = query.filter(ScheduledJobRun.job_name == definition.name)
    if status:
        normalized_status = str(status).strip().lower()
        if normalized_status not in JOB_RUN_STATUSES:
            raise HTTPException(status_code=400, detail="Scheduled job status filter is invalid")
        query = query.filter(ScheduledJobRun.status == normalized_status)
    if trigger:
        normalized_trigger = str(trigger).strip().lower()
        if normalized_trigger not in JOB_RUN_TRIGGERS:
            raise HTTPException(status_code=400, detail="Scheduled job trigger filter is invalid")
        query = query.filter(ScheduledJobRun.trigger == normalized_trigger)

    total = query.count()
    rows = (
        query.order_by(ScheduledJobRun.started_at.desc(), ScheduledJobRun.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "items": [_run_payload(row, include_result=False) for row in rows],
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
    }


@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, _HISTORY_PERMISSION)
    run = db.query(ScheduledJobRun).filter(ScheduledJobRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Scheduled job run not found")
    return _run_payload(run)


@router.post("/{job_name}/run")
def run_registered_job(
    job_name: str,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    definition = _require_definition(job_name)
    if not definition.manual_enabled:
        raise HTTPException(status_code=409, detail="Manual execution is disabled for this job")
    require_permission(db, admin, definition.permission)
    # Release the request transaction and connection before a potentially long job.
    db.rollback()
    return _execute_and_audit(definition=definition, admin=admin, audit_db=db)


@router.post("/runs/{run_id}/retry")
def retry_registered_job(
    run_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    source = (
        db.query(ScheduledJobRun)
        .filter(ScheduledJobRun.id == run_id)
        .with_for_update()
        .first()
    )
    if source is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Scheduled job run not found")
    definition = _require_definition(source.job_name)
    if not definition.retry_enabled or not definition.manual_enabled:
        db.rollback()
        raise HTTPException(status_code=409, detail="Retry is disabled for this job")
    if source.status not in {"failed", "skipped"}:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Only failed or skipped scheduled job runs can be retried",
        )
    source_run_id = source.id
    require_permission(db, admin, definition.permission)
    db.rollback()
    return _execute_and_audit(
        definition=definition,
        admin=admin,
        audit_db=db,
        source_run_id=source_run_id,
    )
