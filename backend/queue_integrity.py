from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, Column, DateTime, Index, String, Text, event, text

from .models import BusinessEvent, MediaProcessingJob

EVENT_MAX_ATTEMPTS = 10
MEDIA_MAX_ATTEMPTS = 5
QUEUE_LEASE_TOKEN_LENGTH = 32
QUEUE_ERROR_MAX_LENGTH = 2_000
EVENT_STATUSES = frozenset({"pending", "processing", "processed", "failed"})
MEDIA_JOB_STATUSES = frozenset({"pending", "processing", "processed", "failed"})


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def _check(table, name: str, expression: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(CheckConstraint(expression, name=name))


def _late_column(model, name: str, column: Column) -> None:
    if not hasattr(model, name):
        setattr(model, name, column)


def _install_columns() -> None:
    _late_column(
        BusinessEvent,
        "next_attempt_at",
        Column(DateTime, nullable=True, default=datetime.utcnow),
    )
    _late_column(
        BusinessEvent,
        "lease_token",
        Column(String(QUEUE_LEASE_TOKEN_LENGTH), nullable=False, default=""),
    )
    _late_column(
        BusinessEvent,
        "lease_expires_at",
        Column(DateTime, nullable=True),
    )
    _late_column(
        BusinessEvent,
        "last_error",
        Column(Text, nullable=False, default=""),
    )

    _late_column(
        MediaProcessingJob,
        "next_attempt_at",
        Column(DateTime, nullable=True, default=datetime.utcnow),
    )
    _late_column(
        MediaProcessingJob,
        "lease_token",
        Column(String(QUEUE_LEASE_TOKEN_LENGTH), nullable=False, default=""),
    )
    _late_column(
        MediaProcessingJob,
        "lease_expires_at",
        Column(DateTime, nullable=True),
    )


def _install_constraints() -> None:
    event_table = BusinessEvent.__table__
    media_table = MediaProcessingJob.__table__

    _check(
        event_table,
        "ck_business_events_status_valid",
        "status IN ('failed', 'pending', 'processed', 'processing')",
    )
    _check(
        event_table,
        "ck_business_events_attempts_range",
        f"attempts BETWEEN 0 AND {EVENT_MAX_ATTEMPTS}",
    )
    _check(
        event_table,
        "ck_business_events_lease_token_size",
        f"length(lease_token) IN (0, {QUEUE_LEASE_TOKEN_LENGTH})",
    )
    _check(
        event_table,
        "ck_business_events_error_size",
        f"length(last_error) <= {QUEUE_ERROR_MAX_LENGTH}",
    )
    _check(
        event_table,
        "ck_business_events_state_coherent",
        "((status = 'pending' AND attempts < 10 AND processed_at IS NULL "
        "AND next_attempt_at IS NOT NULL AND lease_token = '' AND lease_expires_at IS NULL "
        "AND (attempts = 0 OR length(trim(last_error)) > 0)) "
        "OR (status = 'processing' AND attempts < 10 AND processed_at IS NULL "
        "AND next_attempt_at IS NULL AND length(lease_token) = 32 AND lease_expires_at IS NOT NULL) "
        "OR (status = 'processed' AND processed_at IS NOT NULL AND next_attempt_at IS NULL "
        "AND lease_token = '' AND lease_expires_at IS NULL AND last_error = '') "
        "OR (status = 'failed' AND attempts = 10 AND processed_at IS NULL "
        "AND next_attempt_at IS NULL AND lease_token = '' AND lease_expires_at IS NULL "
        "AND length(trim(last_error)) > 0))",
    )

    _check(
        media_table,
        "ck_media_processing_jobs_status_valid",
        "status IN ('failed', 'pending', 'processed', 'processing')",
    )
    _check(
        media_table,
        "ck_media_processing_jobs_attempts_range",
        f"attempts BETWEEN 0 AND {MEDIA_MAX_ATTEMPTS}",
    )
    _check(
        media_table,
        "ck_media_processing_jobs_lease_token_size",
        f"length(lease_token) IN (0, {QUEUE_LEASE_TOKEN_LENGTH})",
    )
    _check(
        media_table,
        "ck_media_processing_jobs_error_size",
        f"length(last_error) <= {QUEUE_ERROR_MAX_LENGTH}",
    )
    _check(
        media_table,
        "ck_media_processing_jobs_state_coherent",
        "((status = 'pending' AND attempts < 5 AND processed_at IS NULL "
        "AND next_attempt_at IS NOT NULL AND lease_token = '' AND lease_expires_at IS NULL "
        "AND (attempts = 0 OR length(trim(last_error)) > 0)) "
        "OR (status = 'processing' AND attempts < 5 AND processed_at IS NULL "
        "AND next_attempt_at IS NULL AND length(lease_token) = 32 AND lease_expires_at IS NOT NULL) "
        "OR (status = 'processed' AND processed_at IS NOT NULL AND next_attempt_at IS NULL "
        "AND lease_token = '' AND lease_expires_at IS NULL AND last_error = '') "
        "OR (status = 'failed' AND attempts = 5 AND processed_at IS NULL "
        "AND next_attempt_at IS NULL AND lease_token = '' AND lease_expires_at IS NULL "
        "AND length(trim(last_error)) > 0))",
    )

    if "uq_media_processing_jobs_active_asset" not in _index_names(media_table):
        predicate = text("status IN ('pending', 'processing')")
        Index(
            "uq_media_processing_jobs_active_asset",
            media_table.c.media_asset_id,
            unique=True,
            postgresql_where=predicate,
            sqlite_where=predicate,
        )


def _normalize_attempts(value: object, maximum: int) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Queue attempts must be an integer") from exc
    if normalized < 0 or normalized > maximum:
        raise HTTPException(status_code=400, detail="Queue attempts are out of range")
    return normalized


def _normalize_common(target, *, allowed_statuses: frozenset[str], maximum: int) -> None:
    target.status = str(target.status or "pending").strip().lower()
    target.attempts = _normalize_attempts(target.attempts, maximum)
    target.lease_token = str(getattr(target, "lease_token", "") or "").strip().lower()
    target.last_error = str(getattr(target, "last_error", "") or "").strip()[:QUEUE_ERROR_MAX_LENGTH]

    if target.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Queue status is invalid")
    if target.lease_token and len(target.lease_token) != QUEUE_LEASE_TOKEN_LENGTH:
        raise HTTPException(status_code=400, detail="Queue lease token is invalid")

    if target.status == "pending":
        if target.attempts >= maximum:
            raise HTTPException(status_code=400, detail="Exhausted queue item cannot remain pending")
        if target.attempts and not target.last_error:
            raise HTTPException(status_code=400, detail="Retried queue item requires an error")
        target.next_attempt_at = getattr(target, "next_attempt_at", None) or datetime.utcnow()
        target.lease_token = ""
        target.lease_expires_at = None
        target.processed_at = None
        return

    if target.status == "processing":
        if target.attempts >= maximum:
            raise HTTPException(status_code=400, detail="Exhausted queue item cannot be processing")
        if len(target.lease_token) != QUEUE_LEASE_TOKEN_LENGTH or target.lease_expires_at is None:
            raise HTTPException(status_code=400, detail="Processing queue item requires a lease")
        target.next_attempt_at = None
        target.processed_at = None
        return

    target.next_attempt_at = None
    target.lease_token = ""
    target.lease_expires_at = None
    if target.status == "processed":
        if target.processed_at is None:
            raise HTTPException(status_code=400, detail="Processed queue item requires a timestamp")
        target.last_error = ""
        return

    target.processed_at = None
    if target.attempts != maximum or not target.last_error:
        raise HTTPException(status_code=400, detail="Failed queue item is inconsistent")


def _validate_event(_mapper, _connection, target: BusinessEvent) -> None:
    _normalize_common(target, allowed_statuses=EVENT_STATUSES, maximum=EVENT_MAX_ATTEMPTS)


def _validate_media_job(_mapper, _connection, target: MediaProcessingJob) -> None:
    _normalize_common(target, allowed_statuses=MEDIA_JOB_STATUSES, maximum=MEDIA_MAX_ATTEMPTS)


def _register_validation() -> None:
    for model, listener in (
        (BusinessEvent, _validate_event),
        (MediaProcessingJob, _validate_media_job),
    ):
        for event_name in ("before_insert", "before_update"):
            if not event.contains(model, event_name, listener):
                event.listen(model, event_name, listener)


_install_columns()
_install_constraints()
_register_validation()
