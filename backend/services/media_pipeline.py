import os
import uuid
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import MediaAsset, MediaDerivative

_MAX_MEDIA_BYTES = 10 * 1024 * 1024
_MAX_MEDIA_DIMENSION = 12_000


def _safe_media_path(media_dir: Path, filename: str) -> Path:
    clean_name = Path(filename or "").name
    if not clean_name or clean_name != filename:
        raise ValueError("Invalid media storage key")
    path = (media_dir / clean_name).resolve()
    if path.parent != media_dir:
        raise ValueError("Invalid media storage path")
    return path


def _save_webp_atomic(image: Image.Image, target: Path, quality: int) -> int:
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        image.save(temporary, format="WEBP", quality=quality, method=4)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        size_bytes = temporary.stat().st_size
        if size_bytes < 1 or size_bytes > _MAX_MEDIA_BYTES:
            raise ValueError("Generated derivative size is invalid")
        temporary.replace(target)
        return size_bytes
    finally:
        temporary.unlink(missing_ok=True)


def generate_local_derivative_payloads(asset: MediaAsset) -> list[dict]:
    """Generate derivative files without touching a SQLAlchemy session.

    This function is safe to run in a worker thread from the async upload
    endpoint. Database rows are created afterwards on the request thread.
    """

    settings = get_settings()
    if settings.media_storage != "local":
        return []

    media_dir = Path(settings.media_local_dir).resolve()
    media_dir.mkdir(parents=True, exist_ok=True)
    source = _safe_media_path(media_dir, asset.storage_key)
    if not source.is_file():
        raise ValueError("Source media file is missing")

    payloads: list[dict] = []
    stem = asset.storage_key.rsplit(".", 1)[0]
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        if image.width < 1 or image.height < 1:
            raise ValueError("Source media dimensions are invalid")
        if image.width > _MAX_MEDIA_DIMENSION or image.height > _MAX_MEDIA_DIMENSION:
            raise ValueError("Source media dimensions are too large")
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA" if "transparency" in opened.info else "RGB")

        if settings.media_generate_thumbnails:
            thumbnail = image.copy()
            thumbnail_width = max(
                1,
                min(int(settings.media_thumbnail_width), _MAX_MEDIA_DIMENSION),
            )
            thumbnail.thumbnail((thumbnail_width, _MAX_MEDIA_DIMENSION))
            key = f"thumb_{stem}.webp"
            target = _safe_media_path(media_dir, key)
            size_bytes = _save_webp_atomic(thumbnail, target, quality=82)
            payloads.append(
                {
                    "derivative_type": "thumbnail",
                    "url": f"{settings.media_public_base_url.rstrip('/')}/{key}",
                    "storage_key": key,
                    "width": thumbnail.width,
                    "height": thumbnail.height,
                    "content_type": "image/webp",
                    "size_bytes": size_bytes,
                }
            )

        if settings.media_generate_webp:
            webp = image.copy()
            key = f"webp_{stem}.webp"
            target = _safe_media_path(media_dir, key)
            size_bytes = _save_webp_atomic(webp, target, quality=86)
            payloads.append(
                {
                    "derivative_type": "webp",
                    "url": f"{settings.media_public_base_url.rstrip('/')}/{key}",
                    "storage_key": key,
                    "width": webp.width,
                    "height": webp.height,
                    "content_type": "image/webp",
                    "size_bytes": size_bytes,
                }
            )
    return payloads


def upsert_media_derivatives(
    db: Session,
    asset: MediaAsset,
    payloads: list[dict],
) -> list[MediaDerivative]:
    existing = {
        row.derivative_type: row
        for row in db.query(MediaDerivative)
        .filter(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type.in_(("thumbnail", "webp")),
        )
        .with_for_update()
        .all()
    }
    derivatives: list[MediaDerivative] = []
    for payload in payloads:
        derivative_type = payload["derivative_type"]
        derivative = existing.get(derivative_type)
        if derivative is None:
            derivative = MediaDerivative(
                media_asset_id=asset.id,
                derivative_type=derivative_type,
            )
            db.add(derivative)
        for field in (
            "url",
            "storage_key",
            "width",
            "height",
            "content_type",
            "size_bytes",
        ):
            setattr(derivative, field, payload[field])
        derivatives.append(derivative)
    return derivatives


def generate_local_derivatives(db: Session, asset: MediaAsset) -> list[MediaDerivative]:
    """Compatibility wrapper for synchronous callers and tests."""

    return upsert_media_derivatives(
        db,
        asset,
        generate_local_derivative_payloads(asset),
    )
