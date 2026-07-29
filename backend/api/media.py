from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import MediaAsset
from ..schemas import MediaOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.media_pipeline import generate_local_derivatives
from ..services.media_storage import delete_media, save_media
from ..services.rbac import require_permission

router = APIRouter(prefix="/media", tags=["media"])


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
        generate_local_derivatives(db, asset)
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
            },
        )
        db.commit()
        db.refresh(asset)
        return asset
    except ValueError as exc:
        db.rollback()
        if storage_key:
            try:
                delete_media(storage_key)
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        if storage_key:
            try:
                delete_media(storage_key)
            except Exception:
                pass
        raise
    except Exception:
        db.rollback()
        if storage_key:
            try:
                delete_media(storage_key)
            except Exception:
                pass
        raise
    finally:
        await file.close()
