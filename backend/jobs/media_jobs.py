import uuid
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import MediaAsset, MediaProcessingJob
from ..queue_integrity import MEDIA_MAX_ATTEMPTS, QUEUE_ERROR_MAX_LENGTH
from ..services.media_pipeline import generate_local_derivatives

_MEDIA_LEASE = timedelta(minutes=10)
_MAX_QUEUE_BATCH = 500
_MAX_PROCESS_BATCH = 100


def _bounded_error(exc: object, fallback: str = "Media processing failed") -> str:
    value = str(exc or fallback).strip() or fallback
    return value[:QUEUE_ERROR_MAX_LENGTH]


def _retry_at(attempts: int, now: datetime) -> datetime:
    return now + timedelta(minutes=min(60, 2 ** max(1, attempts)))


def _normalize_limit(value: int, *, maximum: int, field: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if normalized < 1 or normalized > maximum:
        raise ValueError(f"{field} is out of range")
    return normalized


def queue_missing_media_jobs(db: Session, limit: int = _MAX_QUEUE_BATCH) -> int:
    normalized_limit = _normalize_limit(
        limit,
        maximum=_MAX_QUEUE_BATCH,
        field="Media queue limit",
    )
    existing_job = (
        db.query(MediaProcessingJob.id)
        .filter(MediaProcessingJob.media_asset_id == MediaAsset.id)
        .exists()
    )
    asset_ids = (
        db.query(MediaAsset.id)
        .filter(~existing_job)
        .order_by(MediaAsset.id.asc())
        .limit(normalized_limit)
        .all()
    )

    queued = 0
    for (asset_id,) in asset_ids:
        try:
            with db.begin_nested():
                db.add(
                    MediaProcessingJob(
                        media_asset_id=asset_id,
                        status="pending",
                        attempts=0,
                        next_attempt_at=datetime.utcnow(),
                        lease_token="",
                        lease_expires_at=None,
                        last_error="",
                        processed_at=None,
                    )
                )
                db.flush()
            queued += 1
        except IntegrityError:
            # Another worker won the active-job uniqueness race.
            continue
    db.commit()
    return queued


def _schedule_failure(
    row: MediaProcessingJob,
    exc: Exception,
    *,
    now: datetime,
    permanent: bool,
) -> None:
    attempts = MEDIA_MAX_ATTEMPTS if permanent else min(
        int(row.attempts or 0) + 1,
        MEDIA_MAX_ATTEMPTS,
    )
    row.attempts = attempts
    row.last_error = _bounded_error(exc)
    row.processed_at = None
    row.lease_token = ""
    row.lease_expires_at = None
    if attempts >= MEDIA_MAX_ATTEMPTS:
        row.status = "failed"
        row.next_attempt_at = None
    else:
        row.status = "pending"
        row.next_attempt_at = _retry_at(attempts, now)


def _recover_expired_leases(db: Session, now: datetime) -> int:
    rows = (
        db.query(MediaProcessingJob)
        .filter(
            MediaProcessingJob.status == "processing",
            MediaProcessingJob.lease_expires_at.is_not(None),
            MediaProcessingJob.lease_expires_at <= now,
        )
        .order_by(MediaProcessingJob.id.asc())
        .with_for_update(skip_locked=True)
        .all()
    )
    for row in rows:
        _schedule_failure(
            row,
            RuntimeError("Media processing lease expired"),
            now=now,
            permanent=False,
        )
    return len(rows)


def _claim_pending_jobs(
    db: Session,
    *,
    limit: int,
    now: datetime,
) -> list[tuple[int, str]]:
    _recover_expired_leases(db, now)
    rows = (
        db.query(MediaProcessingJob)
        .filter(
            MediaProcessingJob.status == "pending",
            MediaProcessingJob.next_attempt_at.is_not(None),
            MediaProcessingJob.next_attempt_at <= now,
        )
        .order_by(MediaProcessingJob.id.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )
    claims: list[tuple[int, str]] = []
    for row in rows:
        token = uuid.uuid4().hex
        row.status = "processing"
        row.next_attempt_at = None
        row.lease_token = token
        row.lease_expires_at = now + _MEDIA_LEASE
        row.processed_at = None
        claims.append((row.id, token))
    db.commit()
    return claims


def _process_claim(db: Session, job_id: int, lease_token: str) -> bool:
    row = (
        db.query(MediaProcessingJob)
        .filter(
            MediaProcessingJob.id == job_id,
            MediaProcessingJob.status == "processing",
            MediaProcessingJob.lease_token == lease_token,
        )
        .with_for_update()
        .first()
    )
    if not row:
        db.rollback()
        return False

    asset = (
        db.query(MediaAsset)
        .filter(MediaAsset.id == row.media_asset_id)
        .with_for_update()
        .first()
    )
    if not asset:
        _schedule_failure(
            row,
            RuntimeError("Media asset not found"),
            now=datetime.utcnow(),
            permanent=True,
        )
        db.commit()
        return False

    try:
        with db.begin_nested():
            generate_local_derivatives(db, asset)
            row.status = "processed"
            row.processed_at = datetime.utcnow()
            row.next_attempt_at = None
            row.lease_token = ""
            row.lease_expires_at = None
            row.last_error = ""
            db.flush()
        db.commit()
        return True
    except Exception as exc:
        current = (
            db.query(MediaProcessingJob)
            .filter(
                MediaProcessingJob.id == job_id,
                MediaProcessingJob.status == "processing",
                MediaProcessingJob.lease_token == lease_token,
            )
            .with_for_update()
            .first()
        )
        if current is None:
            db.rollback()
            return False
        _schedule_failure(
            current,
            exc,
            now=datetime.utcnow(),
            permanent=False,
        )
        db.commit()
        return False


def process_media_jobs(db: Session, limit: int = 20) -> int:
    normalized_limit = _normalize_limit(
        limit,
        maximum=_MAX_PROCESS_BATCH,
        field="Media processing limit",
    )
    claims = _claim_pending_jobs(
        db,
        limit=normalized_limit,
        now=datetime.utcnow(),
    )
    processed = 0
    for job_id, lease_token in claims:
        if _process_claim(db, job_id, lease_token):
            processed += 1
    return processed
