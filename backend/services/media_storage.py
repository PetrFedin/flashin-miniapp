import uuid
from pathlib import Path
from fastapi import UploadFile
from ..config import get_settings

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


async def save_media(file: UploadFile) -> dict:
    settings = get_settings()
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("Only jpeg, png and webp images are allowed")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("File is too large. Max 10 MB")

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[file.content_type]
    storage_key = f"{uuid.uuid4().hex}{ext}"

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
            Body=content,
            ContentType=file.content_type,
            ACL="public-read",
        )
        url = f"{settings.media_public_base_url.rstrip('/')}/{storage_key}"
    else:
        media_dir = Path(settings.media_local_dir)
        media_dir.mkdir(parents=True, exist_ok=True)
        target = media_dir / storage_key
        target.write_bytes(content)
        url = f"{settings.media_public_base_url.rstrip('/')}/{storage_key}"

    return {
        "url": url,
        "storage_key": storage_key,
        "filename": file.filename or storage_key,
        "content_type": file.content_type,
        "size_bytes": len(content),
    }
