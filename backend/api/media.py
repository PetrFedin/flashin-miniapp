import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminUser, MediaAsset
from ..schemas import MediaOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.media_pipeline import generate_local_derivatives
from ..services.media_storage import delete_media, save_media
from ..services.rbac import require_permission

router = APIRouter(prefix="/media", tags=["media"])
logger = logging.getLogger(__name__)


async def _cleanup_uploaded_media(storage_key: str) -> None:
    if not storage_key:
        return
    try:
        # S3/R2 deletion is synchronous boto3 I/O; keep compensating cleanup
        # off the FastAPI event loop as well.
        await asyncio.to_thread(delete_media, storage_key)
    except Exception:
        # Preserve the original request failure, but never make orphan cleanup
        # invisible to operations.
        logger.exception(
            "media_compensating_cleanup_failed",
            extra={"storage_key": storage_key},
        )


@router.post("/upload", response_model=MediaOut)
async def upload_media(
    file: UploadFile = File(...),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "media.write")

    # Authentication/RBAC are read-only, but they open the request-scoped
    # SQLAlchemy transaction. Snapshot the only identity needed later and end
    # that transaction before any object-storage side effect.
    admin_id = int(admin.id)
    db.rollback()
    if db.in_transaction():
        raise RuntimeError("Media storage provider call requires a clean DB session")

    storage_key = ""
    try:
        # save_media performs no DB work. S3/R2 and local blocking I/O are
        # executed off the event-loop thread inside the storage service.
        data = await save_media(file)
        storage_key = data["storage_key"]

        # Provider success is followed by a fresh DB transaction. Revalidate
        # the admin identity so the audit FK cannot silently reference an admin
        # deleted/disabled while the external upload was in flight.
        persisted_admin = (
            db.query(AdminUser)
            .filter(AdminUser.id == admin_id, AdminUser.active.is_(True))
            .first()
        )
        if not persisted_admin:
            raise HTTPException(status_code=401, detail="Admin not found")

        asset = MediaAsset(**data)
        db.add(asset)
        db.flush()
        generate_local_derivatives(db, asset)
        log_admin_action(
            db,
            persisted_admin,
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
    except ValueError as exc:
        db.rollback()
        await _cleanup_uploaded_media(storage_key)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        await _cleanup_uploaded_media(storage_key)
        raise
    except Exception:
        db.rollback()
        await _cleanup_uploaded_media(storage_key)
        raise
    finally:
        await file.close()
