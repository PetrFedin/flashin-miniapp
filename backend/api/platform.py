import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..business_event_models import BusinessEventRecoveryState
from ..database import get_db
from ..models import AuditTrail, BusinessEvent, CmsBlock, CmsPage, FeatureFlag, RemoteConfig
from ..schemas import (
    AuditTrailOut,
    CmsBlockIn,
    CmsBlockOut,
    CmsPageIn,
    CmsPageOut,
    FeatureFlagIn,
    FeatureFlagOut,
    RemoteConfigIn,
    RemoteConfigOut,
)
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.event_dispatcher import (
    BusinessEventNotFoundError,
    BusinessEventPayloadError,
    BusinessEventReplayConflictError,
    requeue_failed_event,
)
from ..services.rbac import require_permission

router = APIRouter(prefix="/platform", tags=["platform"])
_EVENT_STATUSES = {"pending", "processed", "failed"}
_PLATFORM_WRITE_PERMISSION = "platform.write"
_EVENT_REPLAY_PERMISSION = "events.replay"


class BusinessEventReplayIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    payload: dict | None = None


def _serialize_event(
    event: BusinessEvent,
    recovery: BusinessEventRecoveryState | None,
    *,
    include_payload: bool = False,
) -> dict:
    result = {
        "id": event.id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "status": event.status,
        "attempts": int(event.attempts or 0),
        "created_at": event.created_at,
        "processed_at": event.processed_at,
        "last_error": recovery.last_error if recovery else "",
        "last_attempt_at": recovery.last_attempt_at if recovery else None,
        "failed_at": recovery.failed_at if recovery else None,
        "replay_count": int(recovery.replay_count or 0) if recovery else 0,
        "last_replayed_at": recovery.last_replayed_at if recovery else None,
        "last_replayed_by_admin_id": (
            recovery.last_replayed_by_admin_id if recovery else None
        ),
        "resolved_at": recovery.resolved_at if recovery else None,
    }
    if include_payload:
        try:
            payload = json.loads(event.payload_json or "{}")
            result["payload"] = payload if isinstance(payload, dict) else None
            result["payload_error"] = (
                "Business event payload must be an object"
                if not isinstance(payload, dict)
                else ""
            )
        except (TypeError, json.JSONDecodeError):
            result["payload"] = None
            result["payload_error"] = "Stored business event payload is invalid JSON"
    return result


@router.get("/features")
def public_features(db: Session = Depends(get_db)):
    return {f.key: f.enabled for f in db.query(FeatureFlag).all()}


@router.post("/admin/features", response_model=FeatureFlagOut)
def upsert_feature(
    payload: FeatureFlagIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, _PLATFORM_WRITE_PERMISSION)
    try:
        row = (
            db.query(FeatureFlag)
            .filter(FeatureFlag.key == payload.key)
            .with_for_update()
            .first()
        )
        if not row:
            row = FeatureFlag(key=payload.key)
            db.add(row)
            db.flush()
        row.enabled = payload.enabled
        row.description = payload.description
        log_admin_action(
            db,
            admin,
            "platform.feature_flag.upsert",
            "feature_flag",
            row.id,
            {"key": row.key, "enabled": row.enabled},
        )
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


@router.get("/remote-config")
def public_remote_config(db: Session = Depends(get_db)):
    return {r.key: json.loads(r.value_json or "{}") for r in db.query(RemoteConfig).all()}


@router.post("/admin/remote-config", response_model=RemoteConfigOut)
def upsert_remote_config(
    payload: RemoteConfigIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, _PLATFORM_WRITE_PERMISSION)
    try:
        row = (
            db.query(RemoteConfig)
            .filter(RemoteConfig.key == payload.key)
            .with_for_update()
            .first()
        )
        if not row:
            row = RemoteConfig(key=payload.key)
            db.add(row)
            db.flush()
        row.value_json = json.dumps(payload.value_json, ensure_ascii=False)
        row.description = payload.description
        # Deliberately do not duplicate remote-config values into the audit trail;
        # configuration may contain operationally sensitive data.
        log_admin_action(
            db,
            admin,
            "platform.remote_config.upsert",
            "remote_config",
            row.id,
            {"key": row.key},
        )
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


@router.get("/cms/pages/{slug}", response_model=CmsPageOut)
def get_page(slug: str, db: Session = Depends(get_db)):
    return (
        db.query(CmsPage)
        .filter(CmsPage.slug == slug, CmsPage.active == True)
        .first()
    )


@router.get("/cms/blocks/{page_slug}", response_model=list[CmsBlockOut])
def get_blocks(page_slug: str, db: Session = Depends(get_db)):
    return (
        db.query(CmsBlock)
        .filter(CmsBlock.page_slug == page_slug, CmsBlock.active == True)
        .order_by(CmsBlock.sort_order)
        .all()
    )


@router.post("/admin/cms/pages", response_model=CmsPageOut)
def upsert_page(
    payload: CmsPageIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    row = db.query(CmsPage).filter(CmsPage.slug == payload.slug).first()
    if not row:
        row = CmsPage(slug=payload.slug)
        db.add(row)
    row.title = payload.title
    row.content_json = json.dumps(payload.content_json, ensure_ascii=False)
    row.active = payload.active
    db.commit()
    db.refresh(row)
    return row


@router.post("/admin/cms/blocks", response_model=CmsBlockOut)
def create_block(
    payload: CmsBlockIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    row = CmsBlock(
        page_slug=payload.page_slug,
        block_type=payload.block_type,
        title=payload.title,
        payload_json=json.dumps(payload.payload_json, ensure_ascii=False),
        sort_order=payload.sort_order,
        active=payload.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/admin/events/summary")
def event_summary(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    counts = {status: 0 for status in _EVENT_STATUSES}
    for status, count in (
        db.query(BusinessEvent.status, func.count(BusinessEvent.id))
        .group_by(BusinessEvent.status)
        .all()
    ):
        counts[str(status)] = int(count)

    oldest_failed_at = (
        db.query(func.min(BusinessEventRecoveryState.failed_at))
        .join(
            BusinessEvent,
            BusinessEvent.id == BusinessEventRecoveryState.business_event_id,
        )
        .filter(BusinessEvent.status == "failed")
        .scalar()
    )
    return {"counts": counts, "oldest_failed_at": oldest_failed_at}


@router.get("/admin/events")
def list_events(
    status: str | None = Query(default=None),
    event_type: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    query = db.query(BusinessEvent, BusinessEventRecoveryState).outerjoin(
        BusinessEventRecoveryState,
        BusinessEventRecoveryState.business_event_id == BusinessEvent.id,
    )
    if status:
        normalized_status = status.strip().lower()
        if normalized_status not in _EVENT_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid business event status")
        query = query.filter(BusinessEvent.status == normalized_status)
    if event_type is not None:
        normalized_event_type = event_type.strip()
        if not normalized_event_type:
            raise HTTPException(status_code=400, detail="Event type cannot be empty")
        query = query.filter(BusinessEvent.event_type == normalized_event_type)

    rows = (
        query.order_by(BusinessEvent.created_at.desc(), BusinessEvent.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_serialize_event(event, recovery) for event, recovery in rows]


@router.get("/admin/events/{event_id}")
def get_event(
    event_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    row = (
        db.query(BusinessEvent, BusinessEventRecoveryState)
        .outerjoin(
            BusinessEventRecoveryState,
            BusinessEventRecoveryState.business_event_id == BusinessEvent.id,
        )
        .filter(BusinessEvent.id == event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Business event not found")
    return _serialize_event(row[0], row[1], include_payload=True)


@router.post("/admin/events/{event_id}/replay")
def replay_event(
    event_id: int,
    payload: BusinessEventReplayIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, _EVENT_REPLAY_PERMISSION)
    reason = payload.reason.strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="Replay reason is too short")

    try:
        event, recovery, before = requeue_failed_event(
            db,
            event_id,
            replacement_payload=payload.payload,
            admin_id=admin.id,
        )
        log_admin_action(
            db,
            admin,
            "business_event.replay",
            "business_event",
            event.id,
            {
                "reason": reason,
                "before": before,
                "after": {
                    "status": event.status,
                    "attempts": event.attempts,
                    "replay_count": recovery.replay_count,
                },
            },
        )
        db.commit()
        db.refresh(event)
        db.refresh(recovery)
        return _serialize_event(event, recovery, include_payload=True)
    except BusinessEventNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BusinessEventReplayConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BusinessEventPayloadError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/admin/audit-trail", response_model=list[AuditTrailOut])
def list_audit_trail(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    return (
        db.query(AuditTrail)
        .order_by(AuditTrail.created_at.desc())
        .limit(200)
        .all()
    )
