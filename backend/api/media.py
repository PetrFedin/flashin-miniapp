from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import MediaAsset
from ..schemas import MediaOut
from ..security import get_current_admin
from ..services.rbac import require_permission
from ..services.media_storage import save_media
from ..services.media_pipeline import generate_local_derivatives

router = APIRouter(prefix="/media", tags=["media"])


@router.post("/upload", response_model=MediaOut)
async def upload_media(file: UploadFile = File(...), admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "media.write")
    try:
        data = await save_media(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    asset = MediaAsset(**data)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    generate_local_derivatives(db, asset)
    db.commit()
    return asset
