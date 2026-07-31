from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Notification
from ..notification_models import NotificationDeliveryState
from ..notification_statuses import (
    NOTIFICATION_FAILED,
    VALID_NOTIFICATION_STATUSES,
)
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.notification_delivery import reset_notification_delivery
from ..services.rbac import require_permission

router = APIRouter(prefix="/admin/notification-delivery", tags=["admin-notifications"])


def _serialize(notification: Notification, state: NotificationDeliveryState | None) -> dict:
    return {
        "id": notification.id,
        "telegram_id": notification.telegram_id,
        "message": notification.message,
        "status": notification.status,
        "error": notification.error,
        "created_at": notification.created_at,
        "sent_at": notification.sent_at,
        "attempts": state.attempts if state else 0,
        "next_attempt_at": state.next_attempt_at if state else None,
        "last_error": state.last_error if state else "",
        "deduplication_key": state.deduplication_key if state else "",
        "leased": bool(state and state.lease_token),
    }


@router.get("")
def list_notification_delivery(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "notifications.read")
    query = (
        db.query(Notification, NotificationDeliveryState)
        .outerjoin(
            NotificationDeliveryState,
            NotificationDeliveryState.notification_id == Notification.id,
        )
    )
    if status:
        normalized_status = status.strip().lower()
        if normalized_status not in VALID_NOTIFICATION_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid notification status")
        query = query.filter(Notification.status == normalized_status)

    rows = (
        query.order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize(notification, state) for notification, state in rows]


@router.post("/failed/requeue")
def requeue_failed_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "notifications.retry")
    try:
        notifications = (
            db.query(Notification)
            .filter(Notification.status == NOTIFICATION_FAILED)
            .order_by(Notification.created_at.asc(), Notification.id.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        reset_at = datetime.utcnow()
        requeued_ids: list[int] = []
        for notification in notifications:
            state = (
                db.query(NotificationDeliveryState)
                .filter(NotificationDeliveryState.notification_id == notification.id)
                .with_for_update()
                .first()
            )
            state = reset_notification_delivery(notification, state, now=reset_at)
            if state.id is None:
                db.add(state)
            requeued_ids.append(notification.id)

        log_admin_action(
            db,
            admin,
            "notification.requeue_batch",
            "notification",
            "",
            {"count": len(requeued_ids), "notification_ids": requeued_ids[:100]},
        )
        db.commit()
        return {"ok": True, "requeued": len(requeued_ids), "notification_ids": requeued_ids}
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Notification retry state changed concurrently") from exc
    except Exception:
        db.rollback()
        raise


@router.post("/{notification_id}/requeue")
def requeue_notification(
    notification_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "notifications.retry")
    try:
        notification = (
            db.query(Notification)
            .filter(Notification.id == notification_id)
            .with_for_update()
            .first()
        )
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")

        state = (
            db.query(NotificationDeliveryState)
            .filter(NotificationDeliveryState.notification_id == notification.id)
            .with_for_update()
            .first()
        )
        previous_status = notification.status
        previous_attempts = state.attempts if state else 0
        state = reset_notification_delivery(notification, state)
        if state.id is None:
            db.add(state)

        log_admin_action(
            db,
            admin,
            "notification.requeue",
            "notification",
            notification.id,
            {
                "previous_status": previous_status,
                "previous_attempts": previous_attempts,
            },
        )
        db.commit()
        db.refresh(notification)
        db.refresh(state)
        return _serialize(notification, state)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Notification retry state changed concurrently") from exc
    except Exception:
        db.rollback()
        raise
