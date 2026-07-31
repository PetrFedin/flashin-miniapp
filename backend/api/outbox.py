from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db, utcnow_naive
from ..models import WebhookOutbox
from ..schemas import WebhookOutboxOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.rbac import require_permission
from ..services.webhook_security import is_internal_destination, normalize_webhook_url

router = APIRouter(prefix="/outbox", tags=["outbox"])

_RETRYABLE_STATUSES = {"pending", "failed"}
_LISTABLE_STATUSES = _RETRYABLE_STATUSES | {"sent", "discarded"}


def _reset_for_retry(row: WebhookOutbox, now: datetime) -> None:
    if row.status == "sent":
        raise HTTPException(status_code=409, detail="Sent webhook cannot be retried")
    if row.status == "discarded":
        raise HTTPException(status_code=409, detail="Discarded webhook cannot be retried")
    if row.status not in _RETRYABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Webhook in status {row.status} cannot be retried")

    if not is_internal_destination(row.destination):
        try:
            row.destination = normalize_webhook_url(row.destination)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Webhook destination is invalid; discard the row or fix the destination: {exc}",
            ) from exc

    row.status = "pending"
    row.attempts = 0
    row.last_error = ""
    row.next_attempt_at = now


@router.get("", response_model=list[WebhookOutboxOut])
def list_outbox(
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "webhooks.read")
    query = db.query(WebhookOutbox)
    if status:
        normalized_status = status.strip().lower()
        if normalized_status not in _LISTABLE_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid outbox status")
        query = query.filter(WebhookOutbox.status == normalized_status)
    return query.order_by(WebhookOutbox.created_at.desc(), WebhookOutbox.id.desc()).limit(limit).all()


@router.post("/failed/requeue")
def retry_failed_outbox(
    limit: int = Query(default=50, ge=1, le=200),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "webhooks.write")
    try:
        rows = (
            db.query(WebhookOutbox)
            .filter(WebhookOutbox.status == "failed")
            .order_by(WebhookOutbox.created_at.asc(), WebhookOutbox.id.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        now = utcnow_naive()
        retried_ids: list[int] = []
        skipped: list[dict] = []
        for row in rows:
            try:
                _reset_for_retry(row, now)
                retried_ids.append(row.id)
            except HTTPException as exc:
                skipped.append({"id": row.id, "reason": str(exc.detail)[:255]})

        log_admin_action(
            db,
            admin,
            "webhook_outbox.requeue_batch",
            "webhook_outbox",
            "",
            {
                "retried": len(retried_ids),
                "retried_ids": retried_ids[:100],
                "skipped": skipped[:100],
            },
        )
        db.commit()
        return {
            "ok": True,
            "retried": len(retried_ids),
            "retried_ids": retried_ids,
            "skipped": skipped,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/{row_id}/retry")
def retry_outbox(
    row_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "webhooks.write")
    try:
        row = (
            db.query(WebhookOutbox)
            .filter(WebhookOutbox.id == row_id)
            .with_for_update()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Webhook outbox row not found")

        previous = {
            "status": row.status,
            "attempts": row.attempts,
            "last_error": row.last_error[:500],
        }
        _reset_for_retry(row, utcnow_naive())
        log_admin_action(
            db,
            admin,
            "webhook_outbox.requeue",
            "webhook_outbox",
            row.id,
            previous,
        )
        db.commit()
        return {"ok": True, "id": row.id, "status": row.status, "attempts": row.attempts}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/{row_id}/discard")
def discard_outbox(
    row_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "webhooks.write")
    try:
        row = (
            db.query(WebhookOutbox)
            .filter(WebhookOutbox.id == row_id)
            .with_for_update()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Webhook outbox row not found")
        if row.status == "sent":
            raise HTTPException(status_code=409, detail="Sent webhook cannot be discarded")
        if row.status == "discarded":
            return {"ok": True, "id": row.id, "status": row.status, "idempotent": True}

        previous_status = row.status
        row.status = "discarded"
        row.next_attempt_at = None
        row.last_error = "Discarded by administrator"
        log_admin_action(
            db,
            admin,
            "webhook_outbox.discard",
            "webhook_outbox",
            row.id,
            {"previous_status": previous_status, "attempts": row.attempts},
        )
        db.commit()
        return {"ok": True, "id": row.id, "status": row.status}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
