import io
import warnings
import uuid
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import get_settings

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_CONTENT_TYPE_FORMATS = {
    "image/jpeg": {"JPEG"},
    "image/png": {"PNG"},
    "image/webp": {"WEBP"},
}
_FORMAT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_FORMAT_CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_IMAGE_DIMENSION = 12_000
_READ_CHUNK_BYTES = 1024 * 1024


async def _read_limited(file: UploadFile) -> bytes:
    content = bytearray()
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > _MAX_UPLOAD_BYTES:
            raise ValueError("File is too large. Max 10 MB")
    if not content:
        raise ValueError("File is empty")
    return bytes(content)


def _safe_filename(filename: str | None, fallback: str) -> str:
    normalized = (filename or "").replace("\\", "/")
    cleaned = Path(normalized).name.replace("\x00", "").strip()
    return (cleaned or fallback)[:255]


def _sanitize_image(content: bytes, declared_content_type: str) -> tuple[bytes, str, str]:
    if declared_content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("Only jpeg, png and webp images are allowed")

    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                source.verify()

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                actual_format = str(source.format or "").upper()
                if actual_format not in _CONTENT_TYPE_FORMATS[declared_content_type]:
                    raise ValueError("Declared image type does not match file content")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("Animated images are not supported")
                width, height = source.size
                if width < 1 or height < 1:
                    raise ValueError("Image dimensions are invalid")
                if width > _MAX_IMAGE_DIMENSION or height > _MAX_IMAGE_DIMENSION:
                    raise ValueError("Image dimensions are too large")
                if width * height > _MAX_IMAGE_PIXELS:
                    raise ValueError("Image has too many pixels")

                image = ImageOps.exif_transpose(source)
                if actual_format == "JPEG":
                    image = image.convert("RGB")
                elif image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in source.info else "RGB")

                output = io.BytesIO()
                if actual_format == "JPEG":
                    image.save(output, format="JPEG", quality=92, optimize=True)
                elif actual_format == "PNG":
                    image.save(output, format="PNG", optimize=True)
                else:
                    image.save(output, format="WEBP", quality=90, method=4)
                sanitized = output.getvalue()
                if not sanitized or len(sanitized) > _MAX_UPLOAD_BYTES:
                    raise ValueError("Sanitized image exceeds the upload limit")
                return (
                    sanitized,
                    _FORMAT_CONTENT_TYPES[actual_format],
                    _FORMAT_EXTENSIONS[actual_format],
                )
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Uploaded file is not a valid image") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


async def save_media(file: UploadFile) -> dict:
    settings = get_settings()
    declared_content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    content = await _read_limited(file)
    sanitized, content_type, extension = _sanitize_image(content, declared_content_type)
    storage_key = f"{uuid.uuid4().hex}{extension}"

    if settings.media_storage in {"s3", "r2"}:
        import boto3

        session = boto3.session.Session()
        client = session.client(
            "s3",
            region_name=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=storage_key,
            Body=sanitized,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
        url = f"{settings.media_public_base_url.rstrip('/')}/{storage_key}"
    else:
        media_dir = Path(settings.media_local_dir).resolve()
        media_dir.mkdir(parents=True, exist_ok=True)
        target = (media_dir / storage_key).resolve()
        if target.parent != media_dir:
            raise ValueError("Invalid media storage path")
        target.write_bytes(sanitized)
        url = f"{settings.media_public_base_url.rstrip('/')}/{storage_key}"

    return {
        "url": url,
        "storage_key": storage_key,
        "filename": _safe_filename(file.filename, storage_key),
        "content_type": content_type,
        "size_bytes": len(sanitized),
    }


def delete_media(storage_key: str) -> None:
    settings = get_settings()
    key = Path(storage_key or "").name
    if not key or key != storage_key:
        return

    if settings.media_storage in {"s3", "r2"}:
        import boto3

        session = boto3.session.Session()
        client = session.client(
            "s3",
            region_name=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
        return

    media_dir = Path(settings.media_local_dir).resolve()
    stem = key.rsplit(".", 1)[0]
    for candidate_name in (key, f"thumb_{stem}.webp", f"webp_{stem}.webp"):
        candidate = (media_dir / candidate_name).resolve()
        if candidate.parent == media_dir:
            candidate.unlink(missing_ok=True)
