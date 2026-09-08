import asyncio
import io
from types import SimpleNamespace

import boto3
import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import media as media_api
from backend.models import AdminUser, MediaAsset
from backend.services import media_storage


class _Upload:
    def __init__(self, content: bytes, *, filename: str = "upload.png", content_type: str = "image/png"):
        self._content = content
        self._offset = 0
        self.filename = filename
        self.content_type = content_type
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._content) - self._offset
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    async def close(self) -> None:
        self.closed = True


class _FinalizeQuery:
    def __init__(self, db):
        self.db = db

    def filter(self, *_args, **_kwargs):
        return self

    def one_or_none(self):
        return self.db.finalize_admin


class _Session:
    def __init__(self, *, finalize_admin=None, fail_flush: bool = False):
        # Simulate the request transaction opened by get_current_admin.
        self.active = True
        self.finalize_admin = finalize_admin
        self.fail_flush = fail_flush
        self.rollbacks = 0
        self.commits = 0
        self.query_calls = 0
        self.added = []

    def in_transaction(self):
        return self.active

    def rollback(self):
        self.rollbacks += 1
        self.active = False

    def query(self, entity):
        assert entity is AdminUser
        assert self.active is False
        self.query_calls += 1
        self.active = True
        return _FinalizeQuery(self)

    def add(self, value):
        assert self.active is True
        self.added.append(value)

    def flush(self):
        assert self.active is True
        if self.fail_flush:
            raise RuntimeError("simulated DB finalize failure")
        for value in self.added:
            if isinstance(value, MediaAsset) and value.id is None:
                value.id = 101

    def commit(self):
        assert self.active is True
        self.commits += 1
        self.active = False

    def refresh(self, _value):
        return None


def _png_bytes() -> bytes:
    image = Image.new("RGB", (16, 12), (100, 120, 140))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _s3_settings(**overrides):
    values = {
        "media_storage": "s3",
        "media_public_base_url": "https://cdn.flashin.test",
        "media_local_dir": "media",
        "s3_bucket": "flashin-media",
        "s3_region": "auto",
        "s3_endpoint_url": "https://storage.flashin.test",
        "s3_access_key_id": "access",
        "s3_secret_access_key": "secret",
        "s3_connect_timeout_seconds": 5,
        "s3_read_timeout_seconds": 20,
        "s3_max_attempts": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_finalize_side_effects(monkeypatch, db, permission_admin_ids):
    def require_permission(_db, admin, permission):
        assert _db is db
        assert db.in_transaction() is True
        assert permission == "media.write"
        permission_admin_ids.append(int(admin.id))

    monkeypatch.setattr(media_api, "require_permission", require_permission)
    monkeypatch.setattr(
        media_api,
        "generate_local_derivatives",
        lambda _db, _asset: [] if _db.in_transaction() else (_ for _ in ()).throw(
            AssertionError("derivatives must run in the fresh DB finalize transaction")
        ),
    )

    def log_action(_db, admin, *_args, **_kwargs):
        assert _db.in_transaction() is True
        assert admin is db.finalize_admin

    monkeypatch.setattr(media_api, "log_admin_action", log_action)


def test_s3_put_runs_after_request_transaction_ends_and_finalize_is_fresh(monkeypatch):
    initial_admin = SimpleNamespace(id=7)
    finalize_admin = SimpleNamespace(id=7, active=True)
    db = _Session(finalize_admin=finalize_admin)
    permission_admin_ids = []
    _patch_finalize_side_effects(monkeypatch, db, permission_admin_ids)
    monkeypatch.setattr(media_storage, "get_settings", lambda: _s3_settings())

    put_calls = []

    class _S3:
        def put_object(self, **kwargs):
            assert db.in_transaction() is False
            put_calls.append(kwargs)

    monkeypatch.setattr(media_storage, "_s3_client", lambda: _S3())
    monkeypatch.setattr(media_api, "delete_media", lambda _key: None)
    upload = _Upload(_png_bytes())

    asset = asyncio.run(media_api.upload_media(file=upload, admin=initial_admin, db=db))

    assert isinstance(asset, MediaAsset)
    assert asset.id == 101
    assert asset.storage_key.endswith(".png")
    assert asset.url.startswith("https://cdn.flashin.test/")
    assert len(put_calls) == 1
    assert put_calls[0]["Bucket"] == "flashin-media"
    assert put_calls[0]["Key"] == asset.storage_key
    assert db.query_calls == 1
    assert db.rollbacks == 1
    assert db.commits == 1
    assert permission_admin_ids == [7, 7]
    assert upload.closed is True


def test_storage_timeout_never_enters_db_finalize(monkeypatch):
    initial_admin = SimpleNamespace(id=8)
    db = _Session(finalize_admin=SimpleNamespace(id=8, active=True))
    monkeypatch.setattr(media_api, "require_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(media_storage, "get_settings", lambda: _s3_settings())

    class _FailingS3:
        def put_object(self, **_kwargs):
            assert db.in_transaction() is False
            raise TimeoutError("simulated object-store timeout")

    monkeypatch.setattr(media_storage, "_s3_client", lambda: _FailingS3())
    upload = _Upload(_png_bytes())

    with pytest.raises(TimeoutError, match="object-store timeout"):
        asyncio.run(media_api.upload_media(file=upload, admin=initial_admin, db=db))

    assert db.query_calls == 0
    assert db.commits == 0
    assert db.in_transaction() is False
    assert upload.closed is True


def test_authorization_change_after_provider_success_fails_closed_and_cleans_up(monkeypatch):
    initial_admin = SimpleNamespace(id=9)
    db = _Session(finalize_admin=None)
    monkeypatch.setattr(media_api, "require_permission", lambda *_args, **_kwargs: None)

    async def save_media(_file):
        assert db.in_transaction() is False
        return {
            "url": "https://cdn.flashin.test/object.png",
            "storage_key": "object.png",
            "filename": "upload.png",
            "content_type": "image/png",
            "size_bytes": 42,
        }

    deleted = []
    monkeypatch.setattr(media_api, "save_media", save_media)
    monkeypatch.setattr(media_api, "delete_media", deleted.append)
    upload = _Upload(b"ignored")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(media_api.upload_media(file=upload, admin=initial_admin, db=db))

    assert exc_info.value.status_code == 403
    assert deleted == ["object.png"]
    assert db.commits == 0
    assert db.in_transaction() is False


def test_cleanup_failure_is_never_silently_swallowed(monkeypatch):
    initial_admin = SimpleNamespace(id=10)
    db = _Session(finalize_admin=SimpleNamespace(id=10, active=True), fail_flush=True)
    monkeypatch.setattr(media_api, "require_permission", lambda *_args, **_kwargs: None)

    async def save_media(_file):
        assert db.in_transaction() is False
        return {
            "url": "https://cdn.flashin.test/object.png",
            "storage_key": "object.png",
            "filename": "upload.png",
            "content_type": "image/png",
            "size_bytes": 42,
        }

    monkeypatch.setattr(media_api, "save_media", save_media)
    monkeypatch.setattr(
        media_api,
        "delete_media",
        lambda _key: (_ for _ in ()).throw(OSError("storage delete failed")),
    )
    upload = _Upload(b"ignored")

    with pytest.raises(RuntimeError, match="media cleanup failed") as exc_info:
        asyncio.run(media_api.upload_media(file=upload, admin=initial_admin, db=db))

    assert isinstance(exc_info.value.__cause__, OSError)
    assert db.commits == 0
    assert db.in_transaction() is False


def test_s3_client_uses_bounded_standard_retry_configuration(monkeypatch):
    captured = {}

    class _BotoSession:
        def client(self, service, **kwargs):
            captured["service"] = service
            captured.update(kwargs)
            return object()

    monkeypatch.setattr(media_storage, "get_settings", lambda: _s3_settings(
        s3_connect_timeout_seconds=4,
        s3_read_timeout_seconds=17,
        s3_max_attempts=4,
    ))
    monkeypatch.setattr(boto3.session, "Session", lambda: _BotoSession())

    media_storage._s3_client()

    assert captured["service"] == "s3"
    assert captured["config"].connect_timeout == 4
    assert captured["config"].read_timeout == 17
    assert captured["config"].retries["total_max_attempts"] == 4
    assert captured["config"].retries["mode"] == "standard"


def test_local_storage_path_semantics_remain_unchanged(monkeypatch, tmp_path):
    settings = _s3_settings(
        media_storage="local",
        media_local_dir=str(tmp_path),
        media_public_base_url="https://cdn.flashin.test",
    )
    monkeypatch.setattr(media_storage, "get_settings", lambda: settings)
    upload = _Upload(_png_bytes(), filename="../../photo.png")

    data = asyncio.run(media_storage.save_media(upload))

    assert data["filename"] == "photo.png"
    assert data["content_type"] == "image/png"
    assert data["storage_key"].endswith(".png")
    assert (tmp_path / data["storage_key"]).is_file()
    assert data["url"] == f"https://cdn.flashin.test/{data['storage_key']}"
