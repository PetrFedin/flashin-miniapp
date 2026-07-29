from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import MediaAsset, MediaDerivative


def _safe_media_path(media_dir: Path, filename: str) -> Path:
    clean_name = Path(filename or "").name
    if not clean_name or clean_name != filename:
        raise ValueError("Invalid media storage key")
    path = (media_dir / clean_name).resolve()
    if path.parent != media_dir:
        raise ValueError("Invalid media storage path")
    return path


def _save_webp_atomic(image: Image.Image, target: Path, quality: int) -> int:
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        image.save(temporary, format="WEBP", quality=quality, method=4)
        temporary.replace(target)
        return target.stat().st_size
    finally:
        temporary.unlink(missing_ok=True)


def generate_local_derivatives(db: Session, asset: MediaAsset) -> list[MediaDerivative]:
    settings = get_settings()
    if settings.media_storage != "local":
        return []

    media_dir = Path(settings.media_local_dir).resolve()
    media_dir.mkdir(parents=True, exist_ok=True)
    source = _safe_media_path(media_dir, asset.storage_key)
    if not source.is_file():
        raise ValueError("Source media file is missing")

    existing = {
        row.derivative_type: row
        for row in db.query(MediaDerivative)
        .filter(MediaDerivative.media_asset_id == asset.id)
        .all()
    }
    derivatives: list[MediaDerivative] = []
    stem = asset.storage_key.rsplit(".", 1)[0]

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA" if "transparency" in opened.info else "RGB")

        if settings.media_generate_thumbnails:
            thumbnail = image.copy()
            thumbnail.thumbnail(
                (settings.media_thumbnail_width, settings.media_thumbnail_width * 2)
            )
            key = f"thumb_{stem}.webp"
            target = _safe_media_path(media_dir, key)
            size_bytes = _save_webp_atomic(thumbnail, target, quality=82)
            derivative = existing.get("thumbnail")
            if derivative is None:
                derivative = MediaDerivative(
                    media_asset_id=asset.id,
                    derivative_type="thumbnail",
                )
                db.add(derivative)
            derivative.url = f"{settings.media_public_base_url.rstrip('/')}/{key}"
            derivative.storage_key = key
            derivative.width = thumbnail.width
            derivative.height = thumbnail.height
            derivative.content_type = "image/webp"
            derivative.size_bytes = size_bytes
            derivatives.append(derivative)

        if settings.media_generate_webp:
            webp = image.copy()
            key = f"webp_{stem}.webp"
            target = _safe_media_path(media_dir, key)
            size_bytes = _save_webp_atomic(webp, target, quality=86)
            derivative = existing.get("webp")
            if derivative is None:
                derivative = MediaDerivative(
                    media_asset_id=asset.id,
                    derivative_type="webp",
                )
                db.add(derivative)
            derivative.url = f"{settings.media_public_base_url.rstrip('/')}/{key}"
            derivative.storage_key = key
            derivative.width = webp.width
            derivative.height = webp.height
            derivative.content_type = "image/webp"
            derivative.size_bytes = size_bytes
            derivatives.append(derivative)

    return derivatives
