import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import model_constraints as _model_constraints  # noqa: F401
from backend.database import Base
from backend.models import MediaAsset


def _asset(upload_key: str, suffix: str) -> MediaAsset:
    return MediaAsset(
        upload_key=upload_key,
        url=f"https://cdn.flashin.test/{suffix}.png",
        storage_key=f"{suffix}.png",
        filename=f"{suffix}.png",
        content_type="image/png",
        size_bytes=42,
    )


def test_create_all_mirrors_media_upload_key_partial_uniqueness():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    indexes = {
        row["name"]: row
        for row in inspect(engine).get_indexes("media_assets")
    }

    assert indexes["uq_media_assets_upload_key_nonempty"]["unique"] == 1


def test_duplicate_nonempty_upload_key_is_rejected_but_legacy_empty_keys_can_coexist():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    db.add_all([_asset("", "legacy-a"), _asset("", "legacy-b")])
    db.commit()

    db.add(_asset("media-upload-key-authority-0001", "first"))
    db.commit()

    db.add(_asset("media-upload-key-authority-0001", "duplicate"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    assert db.query(MediaAsset).filter(
        MediaAsset.upload_key == "media-upload-key-authority-0001"
    ).count() == 1
    assert db.query(MediaAsset).filter(MediaAsset.upload_key == "").count() == 2
