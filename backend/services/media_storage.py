import asyncio
import io
import logging
import time
import warnings
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from weakref import WeakKeyDictionary

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
_S3_TRANSPORT_MAX_INFLIGHT = 4
_S3_TRANSPORT_EXECUTOR = ThreadPoolExecutor(
    max_workers=_S3_TRANSPORT_MAX_INFLIGHT,
    thread_name_prefix="flashin-s3",
)
_S3_TRANSPORT_GATES = WeakKeyDictionary()
_S3_TRANSPORT_GATES_LOCK = Lock()
logger = logging.getLogger(__name__)

class MediaStorageWriteCancelled(asyncio.CancelledError):
    """Request cancellation after provider write started.

    The sync provider operation is allowed to reach a bounded terminal outcome
    before this cancellation propagates so durable orphan cleanup cannot race a
    still-running put_object.
    """

    def __init__(self, storage_key: str):
        super().__init__("media storage write cancelled")
        self.storage_key = storage_key


class MediaStorageWriteError(RuntimeError):
    """External storage write failed after FLASHIN generated an immutable key.

    The provider may have accepted the object before the client observed the
    exception. Carrying only the server-generated key lets the upload route
    enqueue safe compensating deletion without accepting an operator/client key.
    """

    def __init__(self, storage_key: str, message: str = "media storage write failed"):
        super().__init__(message)
        self.storage_key = storage_key



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


def _s3_client(settings=None):
    settings = settings or get_settings()
    import boto3
    from botocore.config import Config

    session = boto3.session.Session()
    return session.client(
        "s3",
        region_name=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            retries={
                "total_max_attempts": settings.s3_max_attempts,
                "mode": "standard",
            },
        ),
    )


def _s3_transport_gate() -> asyncio.BoundedSemaphore:
    loop = asyncio.get_running_loop()
    with _S3_TRANSPORT_GATES_LOCK:
        gate = _S3_TRANSPORT_GATES.get(loop)
        if gate is None:
            gate = asyncio.BoundedSemaphore(_S3_TRANSPORT_MAX_INFLIGHT)
            _S3_TRANSPORT_GATES[loop] = gate
        return gate


def _put_s3_object_sync(
    settings,
    storage_key: str,
    body: bytes,
    content_type: str,
) -> None:
    client = _s3_client(settings)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=storage_key,
        Body=body,
        ContentType=content_type,
        CacheControl="public, max-age=31536000, immutable",
    )


async def _put_s3_object_bounded(
    settings,
    *,
    storage_key: str,
    body: bytes,
    content_type: str,
) -> None:
    """Run blocking boto3 I/O off-loop with bounded admission.

    Once the provider call has been submitted, cancellation is deliberately
    delayed until that bounded synchronous call finishes. This prevents release
    of the concurrency slot and durable cleanup while put_object is still
    capable of completing afterwards.
    """

    gate = _s3_transport_gate()
    queued_at = time.monotonic()
    await gate.acquire()
    acquired_at = time.monotonic()
    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(
            _S3_TRANSPORT_EXECUTOR,
            _put_s3_object_sync,
            settings,
            storage_key,
            body,
            content_type,
        )
    except BaseException:
        gate.release()
        raise

    cancelled = False
    try:
        while True:
            try:
                await asyncio.shield(future)
                break
            except asyncio.CancelledError:
                cancelled = True
                if future.done():
                    break
                continue

        elapsed_ms = round((time.monotonic() - acquired_at) * 1000)
        queue_ms = round((acquired_at - queued_at) * 1000)
        if cancelled:
            provider_error = ""
            try:
                future.result()
            except Exception as exc:
                provider_error = exc.__class__.__name__
            logger.info(
                "media_s3_put_cancelled_after_provider_terminal queue_ms=%s elapsed_ms=%s provider_error=%s",
                queue_ms,
                elapsed_ms,
                provider_error,
            )
            raise MediaStorageWriteCancelled(storage_key)

        # Re-raise provider exceptions on the event-loop task after the worker
        # has finished. save_media converts them into MediaStorageWriteError.
        future.result()
        logger.info(
            "media_s3_put_complete queue_ms=%s elapsed_ms=%s",
            queue_ms,
            elapsed_ms,
        )
    finally:
        gate.release()


async def save_media(file: UploadFile) -> dict:
    settings = get_settings()
    declared_content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    content = await _read_limited(file)
    sanitized, content_type, extension = _sanitize_image(content, declared_content_type)
    storage_key = f"{uuid.uuid4().hex}{extension}"

    if settings.media_storage in {"s3", "r2"}:
        try:
            await _put_s3_object_bounded(
                settings,
                storage_key=storage_key,
                body=sanitized,
                content_type=content_type,
            )
        except MediaStorageWriteCancelled:
            raise
        except Exception as exc:
            raise MediaStorageWriteError(storage_key) from exc
        url = f"{settings.media_public_base_url.rstrip('/')}/{storage_key}"
    else:
        media_dir = Path(settings.media_local_dir).resolve()
        media_dir.mkdir(parents=True, exist_ok=True)
        target = (media_dir / storage_key).resolve()
        if target.parent != media_dir:
            raise ValueError("Invalid media storage path")
        try:
            target.write_bytes(sanitized)
        except Exception as exc:
            raise MediaStorageWriteError(storage_key) from exc
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
        client = _s3_client()
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
        return

    media_dir = Path(settings.media_local_dir).resolve()
    stem = key.rsplit(".", 1)[0]
    for candidate_name in (key, f"thumb_{stem}.webp", f"webp_{stem}.webp"):
        candidate = (media_dir / candidate_name).resolve()
        if candidate.parent == media_dir:
            candidate.unlink(missing_ok=True)
