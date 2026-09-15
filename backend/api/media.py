from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminUser, MediaAsset
from ..schemas import MediaOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.media_cleanup import (
    MediaCleanupReviewRequired,
    enqueue_media_cleanup,
    list_media_cleanup_commands,
    requeue_media_cleanup_command,
)
from ..services.media_pipeline import generate_local_derivatives
from ..services.media_storage import MediaStorageWriteError, save_media
from ..services.rbac import require_permission

router = APIRouter(prefix="/media", tags=["media"])


def _end_request_transaction_before_storage_io(db: Session) -> None:
    """Release request-owned DB state before object-storage I/O.

    At this boundary the request has only authenticated/authorized the admin;
    no durable media write has happened yet. Rolling back therefore releases
    the connection without discarding business state.
    """

    if db.in_transaction():
        db.rollback()
    if db.in_transaction():
        raise RuntimeError("database transaction must be closed before media storage I/O")


def _reload_media_admin_for_finalize(db: Session, admin_id: int) -> AdminUser:
    """Start a fresh DB phase and fail closed if authorization changed."""

    admin = (
        db.query(AdminUser)
        .filter(AdminUser.id == admin_id, AdminUser.active.is_(True))
        .one_or_none()
    )
    if admin is None:
        raise HTTPException(status_code=403, detail="Admin authorization changed during media upload")
    require_permission(db, admin, "media.write")
    return admin


def _persist_uploaded_media_cleanup_or_raise(db: Session, storage_key: str) -> None:
    """Create durable cleanup work for the exact generated object key.

    Cleanup is intentionally asynchronous: the request first records recovery
    work in PostgreSQL, while the dedicated worker claims/commits and performs
    storage I/O without an active DB transaction. If persistence itself is
    unavailable, fail loudly rather than pretending recovery is durable.
    """

    if not storage_key:
        return
    try:
        enqueue_media_cleanup(db, storage_key, reason="upload_finalize_failed")
    except Exception as cleanup_exc:
        db.rollback()
        raise RuntimeError("failed to persist media cleanup recovery command") from cleanup_exc


@router.post("/upload", response_model=MediaOut)
async def upload_media(
    file: UploadFile = File(...),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    storage_key = ""
    try:
        require_permission(db, admin, "media.write")
        admin_id = int(admin.id)
        _end_request_transaction_before_storage_io(db)

        data = await save_media(file)
        storage_key = data["storage_key"]

        finalize_admin = _reload_media_admin_for_finalize(db, admin_id)
        asset = MediaAsset(**data)
        db.add(asset)
        db.flush()
        generate_local_derivatives(db, asset)
        log_admin_action(
            db,
            finalize_admin,
            "media.upload",
            "media_asset",
            asset.id,
            {
                "storage_key": asset.storage_key,
                "content_type": asset.content_type,
                "size_bytes": asset.size_bytes,
            },
        )
        db.commit()
        db.refresh(asset)
        return asset
    except MediaStorageWriteError as exc:
        db.rollback()
        _persist_uploaded_media_cleanup_or_raise(db, exc.storage_key)
        raise
    except ValueError as exc:
        db.rollback()
        _persist_uploaded_media_cleanup_or_raise(db, storage_key)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        _persist_uploaded_media_cleanup_or_raise(db, storage_key)
        raise
    except Exception:
        db.rollback()
        _persist_uploaded_media_cleanup_or_raise(db, storage_key)
        raise
    finally:
        await file.close()


@router.get("/cleanup")
def media_cleanup_queue(
    limit: int = 100,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "media.write")
    return {"items": list_media_cleanup_commands(db, limit=limit)}


@router.post("/cleanup/{command_id}/retry")
def retry_media_cleanup(
    command_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "media.write")
    try:
        command, previous_status = requeue_media_cleanup_command(db, command_id)
        log_admin_action(
            db,
            admin,
            "media.cleanup.retry",
            "provider_command",
            command.id,
            {
                "previous_status": previous_status,
                "object_fingerprint": command.aggregate_id,
            },
        )
        db.commit()
        return {
            "id": command.id,
            "status": command.status,
            "object_fingerprint": command.aggregate_id,
        }
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, MediaCleanupReviewRequired) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
