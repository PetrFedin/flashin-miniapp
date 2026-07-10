from pathlib import Path
from PIL import Image
from sqlalchemy.orm import Session
from ..config import get_settings
from ..models import MediaAsset, MediaDerivative


def generate_local_derivatives(db: Session, asset: MediaAsset) -> list[MediaDerivative]:
    settings = get_settings()
    if settings.media_storage != "local":
        return []

    media_dir = Path(settings.media_local_dir)
    source = media_dir / asset.storage_key
    if not source.exists():
        return []

    derivatives = []
    with Image.open(source) as img:
        if settings.media_generate_thumbnails:
            thumb = img.copy()
            thumb.thumbnail((settings.media_thumbnail_width, settings.media_thumbnail_width * 2))
            key = f"thumb_{asset.storage_key.rsplit('.', 1)[0]}.webp"
            target = media_dir / key
            thumb.save(target, format="WEBP", quality=82)
            der = MediaDerivative(
                media_asset_id=asset.id,
                derivative_type="thumbnail",
                url=f"{settings.media_public_base_url.rstrip('/')}/{key}",
                storage_key=key,
                width=thumb.width,
                height=thumb.height,
                content_type="image/webp",
                size_bytes=target.stat().st_size,
            )
            db.add(der)
            derivatives.append(der)

        if settings.media_generate_webp:
            webp = img.copy()
            key = f"webp_{asset.storage_key.rsplit('.', 1)[0]}.webp"
            target = media_dir / key
            webp.save(target, format="WEBP", quality=86)
            der = MediaDerivative(
                media_asset_id=asset.id,
                derivative_type="webp",
                url=f"{settings.media_public_base_url.rstrip('/')}/{key}",
                storage_key=key,
                width=webp.width,
                height=webp.height,
                content_type="image/webp",
                size_bytes=target.stat().st_size,
            )
            db.add(der)
            derivatives.append(der)
    return derivatives
