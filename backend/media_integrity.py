import re

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, Index, event, text

from .models import MediaAsset, MediaDerivative

ALLOWED_MEDIA_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
ALLOWED_DERIVATIVE_TYPES = frozenset({"thumbnail", "webp"})
MAX_MEDIA_BYTES = 10 * 1024 * 1024
MAX_MEDIA_DIMENSION = 12_000
_STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def _check(table, name: str, expression: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(CheckConstraint(expression, name=name))


def _normalize_url(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 2048:
        raise HTTPException(status_code=400, detail="Media URL is invalid")
    return normalized


def _normalize_storage_key(value: object) -> str:
    normalized = str(value or "").strip()
    if not _STORAGE_KEY_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Media storage key is invalid")
    return normalized


def _positive_integer(value: object, *, field: str, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer") from exc
    if normalized < 1 or normalized > maximum:
        raise HTTPException(status_code=400, detail=f"{field} is out of range")
    return normalized


def _validate_asset(_mapper, _connection, target: MediaAsset) -> None:
    target.url = _normalize_url(target.url)
    target.storage_key = _normalize_storage_key(target.storage_key)
    target.filename = str(target.filename or target.storage_key).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not target.filename or len(target.filename) > 255:
        raise HTTPException(status_code=400, detail="Media filename is invalid")
    target.content_type = str(target.content_type or "").strip().lower()
    if target.content_type not in ALLOWED_MEDIA_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported media content type")
    target.size_bytes = _positive_integer(
        target.size_bytes,
        field="Media size",
        maximum=MAX_MEDIA_BYTES,
    )


def _validate_derivative(_mapper, _connection, target: MediaDerivative) -> None:
    target.derivative_type = str(target.derivative_type or "").strip().lower()
    if target.derivative_type not in ALLOWED_DERIVATIVE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported media derivative type")
    target.url = _normalize_url(target.url)
    target.storage_key = _normalize_storage_key(target.storage_key)
    target.content_type = str(target.content_type or "").strip().lower()
    if target.content_type != "image/webp":
        raise HTTPException(status_code=400, detail="Media derivatives must be WebP")
    target.width = _positive_integer(
        target.width,
        field="Derivative width",
        maximum=MAX_MEDIA_DIMENSION,
    )
    target.height = _positive_integer(
        target.height,
        field="Derivative height",
        maximum=MAX_MEDIA_DIMENSION,
    )
    target.size_bytes = _positive_integer(
        target.size_bytes,
        field="Derivative size",
        maximum=MAX_MEDIA_BYTES,
    )


def apply_media_constraints() -> None:
    asset = MediaAsset.__table__
    derivative = MediaDerivative.__table__

    _check(asset, "ck_media_assets_url_normalized", "url = trim(url)")
    _check(asset, "ck_media_assets_url_size", "length(url) BETWEEN 1 AND 2048")
    _check(asset, "ck_media_assets_storage_key_normalized", "storage_key = trim(storage_key)")
    _check(asset, "ck_media_assets_storage_key_size", "length(storage_key) BETWEEN 1 AND 255")
    _check(asset, "ck_media_assets_filename_normalized", "filename = trim(filename)")
    _check(asset, "ck_media_assets_filename_size", "length(filename) BETWEEN 1 AND 255")
    _check(
        asset,
        "ck_media_assets_content_type_valid",
        "content_type IN ('image/jpeg', 'image/png', 'image/webp')",
    )
    _check(
        asset,
        "ck_media_assets_size_range",
        f"size_bytes BETWEEN 1 AND {MAX_MEDIA_BYTES}",
    )
    if "uq_media_assets_storage_key" not in _index_names(asset):
        Index("uq_media_assets_storage_key", asset.c.storage_key, unique=True)

    _check(
        derivative,
        "ck_media_derivatives_type_valid",
        "derivative_type IN ('thumbnail', 'webp') OR derivative_type LIKE 'legacy_duplicate_%'",
    )
    _check(
        derivative,
        "ck_media_derivatives_type_normalized",
        "derivative_type = lower(trim(derivative_type))",
    )
    _check(derivative, "ck_media_derivatives_url_normalized", "url = trim(url)")
    _check(derivative, "ck_media_derivatives_url_size", "length(url) BETWEEN 1 AND 2048")
    _check(
        derivative,
        "ck_media_derivatives_storage_key_normalized",
        "storage_key = trim(storage_key)",
    )
    _check(
        derivative,
        "ck_media_derivatives_storage_key_size",
        "length(storage_key) BETWEEN 1 AND 255",
    )
    _check(
        derivative,
        "ck_media_derivatives_content_type_valid",
        "content_type = 'image/webp'",
    )
    _check(
        derivative,
        "ck_media_derivatives_dimensions_range",
        f"width BETWEEN 1 AND {MAX_MEDIA_DIMENSION} AND height BETWEEN 1 AND {MAX_MEDIA_DIMENSION}",
    )
    _check(
        derivative,
        "ck_media_derivatives_size_range",
        f"size_bytes BETWEEN 1 AND {MAX_MEDIA_BYTES}",
    )
    if "uq_media_derivatives_storage_key" not in _index_names(derivative):
        Index("uq_media_derivatives_storage_key", derivative.c.storage_key, unique=True)
    if "uq_media_derivatives_asset_type" not in _index_names(derivative):
        predicate = text("derivative_type IN ('thumbnail', 'webp')")
        Index(
            "uq_media_derivatives_asset_type",
            derivative.c.media_asset_id,
            derivative.c.derivative_type,
            unique=True,
            postgresql_where=predicate,
            sqlite_where=predicate,
        )


def _register_validation() -> None:
    for model, listener in (
        (MediaAsset, _validate_asset),
        (MediaDerivative, _validate_derivative),
    ):
        for event_name in ("before_insert", "before_update"):
            if not event.contains(model, event_name, listener):
                event.listen(model, event_name, listener)


apply_media_constraints()
_register_validation()
