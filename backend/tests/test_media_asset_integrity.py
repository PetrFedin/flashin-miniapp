import inspect
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.api import media as media_api
from backend.database import Base
from backend.models import MediaAsset, MediaDerivative
from backend.services import media_pipeline, media_storage


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _png_bytes(size=(8, 6)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 128)).save(output, format="PNG")
    return output.getvalue()


def _asset(**overrides) -> MediaAsset:
    values = {
        "url": "/media/asset.png",
        "storage_key": "asset.png",
        "filename": "asset.png",
        "content_type": "image/png",
        "size_bytes": 128,
    }
    values.update(overrides)
    return MediaAsset(**values)


def test_image_sanitizer_validates_declared_type_and_reencodes():
    sanitized, content_type, extension = media_storage._sanitize_image(
        _png_bytes(),
        "image/png",
    )

    assert content_type == "image/png"
    assert extension == ".png"
    assert sanitized.startswith(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="does not match"):
        media_storage._sanitize_image(_png_bytes(), "image/jpeg")


def test_atomic_write_replaces_complete_file_without_temp_artifacts(tmp_path):
    target = tmp_path / "asset.png"
    target.write_bytes(b"old")

    media_storage._atomic_write(target, b"new-complete-content")

    assert target.read_bytes() == b"new-complete-content"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_media_asset_storage_key_is_unique_and_normalized():
    db = _session()
    first = _asset(storage_key=" asset.png ", url=" /media/asset.png ")
    db.add(first)
    db.commit()

    assert first.storage_key == "asset.png"
    assert first.url == "/media/asset.png"

    db.add(_asset(storage_key="asset.png", url="/media/duplicate.png"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_orm_rejects_invalid_media_metadata():
    db = _session()
    db.add(_asset(storage_key="../asset.png"))
    with pytest.raises(HTTPException):
        db.commit()
    db.rollback()

    db.add(_asset(storage_key="asset.svg", content_type="image/svg+xml"))
    with pytest.raises(HTTPException):
        db.commit()
    db.rollback()


def test_derivative_type_and_storage_are_unique():
    db = _session()
    asset = _asset()
    db.add(asset)
    db.flush()
    values = {
        "media_asset_id": asset.id,
        "derivative_type": "thumbnail",
        "url": "/media/thumb_asset.webp",
        "storage_key": "thumb_asset.webp",
        "width": 100,
        "height": 75,
        "content_type": "image/webp",
        "size_bytes": 64,
    }
    db.add(MediaDerivative(**values))
    db.commit()

    db.add(
        MediaDerivative(
            **{
                **values,
                "storage_key": "thumb_asset_duplicate.webp",
                "url": "/media/thumb_asset_duplicate.webp",
            }
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_application_cannot_create_legacy_derivative_quarantine_type():
    db = _session()
    asset = _asset()
    db.add(asset)
    db.flush()
    db.add(
        MediaDerivative(
            media_asset_id=asset.id,
            derivative_type="legacy_duplicate_7",
            url="/media/legacy.webp",
            storage_key="legacy.webp",
            width=1,
            height=1,
            content_type="image/webp",
            size_bytes=1,
        )
    )
    with pytest.raises(HTTPException):
        db.commit()
    db.rollback()


def test_local_derivative_generation_returns_valid_payloads(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        media_storage="local",
        media_local_dir=str(tmp_path),
        media_public_base_url="/media",
        media_generate_thumbnails=True,
        media_thumbnail_width=4,
        media_generate_webp=True,
    )
    monkeypatch.setattr(media_pipeline, "get_settings", lambda: settings)
    (tmp_path / "asset.png").write_bytes(_png_bytes((8, 6)))

    payloads = media_pipeline.generate_local_derivative_payloads(_asset())

    assert {payload["derivative_type"] for payload in payloads} == {"thumbnail", "webp"}
    assert all(payload["content_type"] == "image/webp" for payload in payloads)
    assert all(payload["size_bytes"] > 0 for payload in payloads)
    assert (tmp_path / "thumb_asset.webp").is_file()
    assert (tmp_path / "webp_asset.webp").is_file()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_upload_endpoint_runs_file_processing_off_event_loop():
    source = inspect.getsource(media_api.upload_media)

    assert "await save_media(file)" in source
    assert "await run_in_threadpool" in source
    assert source.index("await run_in_threadpool") < source.index("upsert_media_derivatives")
    assert "await _cleanup_uploaded_media" in source


def test_migration_repairs_legacy_media_before_constraints():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0031_media_asset_integrity.py"
    ).read_text(encoding="utf-8")

    first_repair = source.index("UPDATE media_assets")
    duplicate_repair = source.index("WITH ranked AS")
    first_constraint = source.index("op.create_check_constraint")

    assert first_repair < duplicate_repair < first_constraint
    assert "legacy_duplicate_" in source
    assert "uq_media_assets_storage_key" in source
    assert "uq_media_derivatives_asset_type" in source
    assert 'down_revision = "0030_privacy_request_integrity"' in source
