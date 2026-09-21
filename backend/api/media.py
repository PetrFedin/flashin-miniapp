import re

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
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

_UPLOAD_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,255}$")


def _normalize_upload_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not _UPLOAD_KEY_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid media upload idempotency key")
    return normalized


def _media_response(asset: MediaAsset) -> MediaOut:
    return MediaOut.model_validate(asset)


def _committed_media_response(response: MediaOut) -> MediaOut:
    """Explicit post-commit response phase; never owns object cleanup."""

    return response


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


def _delete_uploaded_media_or_raise(storage_key: str) -> None:
    """Compensate a failed DB finalize without hiding storage failure.

    Durable retry/review for a failed compensation is tracked separately by
    #222. Until that exists, a cleanup failure must remain an explicit error,
    never a swallowed exception.
    """

    if not storage_key:
        return
    try:
        delete_media(storage_key)
    except Exception as cleanup_exc:
        raise RuntimeError(
            f"media cleanup failed for storage object {storage_key}"
        ) from cleanup_exc


@router.get("/uploads/{upload_key}", response_model=MediaOut)
def recover_media_upload(
    upload_key: str,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "media.write")
    normalized_key = _normalize_upload_key(upload_key)
    asset = (
        db.query(MediaAsset)
        .filter(MediaAsset.upload_key == normalized_key)
        .one_or_none()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="Media upload not found")
    return asset


@router.post("/upload", response_model=MediaOut)
async def upload_media(
    file: UploadFile = File(...),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    storage_key = ""
    authoritative_commit_reached = False
    try:
        require_permission(db, admin, "media.write")
        upload_key = _normalize_upload_key(idempotency_key)

        # A client retry after an ambiguous response must recover the committed
        # row before touching object storage again.
        existing = (
            db.query(MediaAsset)
            .filter(MediaAsset.upload_key == upload_key)
            .one_or_none()
        )
        if existing is not None:
            return _media_response(existing)

        admin_id = int(admin.id)
        _end_request_transaction_before_storage_io(db)

        data = await save_media(file)
        storage_key = data["storage_key"]

        finalize_admin = _reload_media_admin_for_finalize(db, admin_id)

        # Close the race where another request with the same idempotency key
        # committed while this request was writing its provider object.
        existing = (
            db.query(MediaAsset)
            .filter(MediaAsset.upload_key == upload_key)
            .one_or_none()
        )
        if existing is not None:
            response = _media_response(existing)
            db.rollback()
            _delete_uploaded_media_or_raise(storage_key)
            storage_key = ""
            return response

        asset = MediaAsset(upload_key=upload_key, **data)
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
                "upload_key": upload_key,
            },
        )

        # Materialize the response while the finalize transaction is still
        # active. After commit there is no refresh/lazy-load step that could
        # misclassify a committed object as an orphan.
        response = _media_response(asset)
        db.commit()
        authoritative_commit_reached = True
        return _committed_media_response(response)
    except ValueError as exc:
        db.rollback()
        if storage_key and not authoritative_commit_reached:
            _delete_uploaded_media_or_raise(storage_key)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        if storage_key and not authoritative_commit_reached:
            _delete_uploaded_media_or_raise(storage_key)
        raise
    except Exception:
        db.rollback()
        if storage_key and not authoritative_commit_reached:
            _delete_uploaded_media_or_raise(storage_key)
        raise
    finally:
        await file.close()

