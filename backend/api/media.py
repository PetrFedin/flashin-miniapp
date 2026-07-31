import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..database import get_db
from ..models import MediaAsset
from ..schemas import MediaOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.media_pipeline import (
    generate_local_derivative_payloads,
    upsert_media_derivatives,
)
from ..services.media_storage import delete_media, save_media
from ..services.rbac import require_permission

router = APIRouter(prefix="/media", tags=["media"])
logger = logging.getLogger(__name__)


async def _cleanup_uploaded_media(storage_key: str) -> None:
    if not storage_key:
        return
    try:
        await run_in_threadpool(delete_media, storage_key)
    except Exception:
        logger.exception("Could not remove orphaned media object %s", storage_key)


@router.post("/upload", response_model=MediaOut)
async def upload_media(
    file: UploadFile = File(...),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "media.write")
    storage_key = ""
    try:
        data = await save_media(file)
        storage_key = data["storage_key"]
        asset = MediaAsset(**data)
        db.add(asset)
        db.flush()

        derivative_payloads = await run_in_threadpool(
            generate_local_derivative_payloads,
            asset,
        )
        upsert_media_derivatives(db, asset, derivative_payloads)
        log_admin_action(
            db,
            admin,
            "media.upload",
            "media_asset",
            asset.id,
            {
                "storage_key": asset.storage_key,
                "content_type": asset.content_type,
                "size_bytes": asset.size_bytes,
                "derivatives": [
                    payload["derivative_type"] for payload in derivative_payloads
                ],
            },
        )
        db.commit()
        db.refresh(asset)
        return asset
    except ValueError as exc:
        db.rollback()
        await _cleanup_uploaded_media(storage_key)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        await _cleanup_uploaded_media(storage_key)
        raise HTTPException(
            status_code=409,
            detail="Media storage key or derivative already exists",
        ) from exc
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
