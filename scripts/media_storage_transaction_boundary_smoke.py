#!/usr/bin/env python3
"""Prove the media DB/object-storage transaction boundary with PostgreSQL.

Only the external S3 transport is replaced. Admin authorization, SQLAlchemy
transaction handling, MediaAsset persistence and AuditLog persistence are real.
The smoke proves:
- an auth/request transaction exists before upload handling;
- S3 put_object runs with no active SQLAlchemy transaction;
- finalize re-enters PostgreSQL and atomically persists media + audit;
- an ambiguous storage timeout never enters DB finalize, creates no MediaAsset and durably queues exact-key cleanup;
- a failure after authoritative commit never queues cleanup for the committed object;
- retry with the same upload key re-reads the committed MediaAsset without a second provider write.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import media as media_api
from backend.database import engine
from backend.models import AdminUser, AuditLog, MediaAsset
from backend.provider_models import ProviderCommand
from backend.services import media_storage
from backend.services.media_cleanup import MEDIA_CLEANUP_PROVIDER, media_cleanup_idempotency_key


class Upload:
    def __init__(self, content: bytes, filename: str) -> None:
        self._content = content
        self._offset = 0
        self.filename = filename
        self.content_type = "image/png"
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._content) - self._offset
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    async def close(self) -> None:
        self.closed = True


class RecordingS3:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.calls: list[dict] = []
        self.failure: Exception | None = None
        self.transaction_clean_checks = 0

    def put_object(self, **kwargs):
        assert self.db.in_transaction() is False
        self.transaction_clean_checks += 1
        self.calls.append(dict(kwargs))
        if self.failure is not None:
            raise self.failure
        return {"ETag": '"smoke"'}


def _png_bytes() -> bytes:
    image = Image.new("RGB", (18, 14), (90, 110, 130))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def main() -> int:
    token = uuid.uuid4().hex[:16]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    original_get_settings = media_storage.get_settings
    original_s3_client = media_storage._s3_client
    original_generate_derivatives = media_api.generate_local_derivatives
    original_committed_response = media_api._committed_media_response

    settings = SimpleNamespace(
        media_storage="s3",
        media_public_base_url="https://cdn.flashin.test",
        media_local_dir="media",
        s3_bucket="flashin-media-smoke",
        s3_region="auto",
        s3_endpoint_url="https://storage.flashin.test",
        s3_access_key_id="smoke-access",
        s3_secret_access_key="smoke-secret",
        s3_connect_timeout_seconds=5,
        s3_read_timeout_seconds=20,
        s3_max_attempts=3,
    )
    transport = RecordingS3(db)

    try:
        admin = AdminUser(
            email=f"media-boundary-{token}@flashin.test",
            password_hash="not-used-by-smoke",
            role="owner",
            active=True,
        )
        db.add(admin)
        db.commit()
        admin_id = int(admin.id)

        media_storage.get_settings = lambda: settings
        media_storage._s3_client = lambda: transport
        # S3/R2 has no local derivative generation. Keep the smoke focused on
        # the real DB finalize while avoiding unrelated local filesystem work.
        media_api.generate_local_derivatives = lambda _db, _asset: []

        db.expire_all()
        request_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).one()
        assert db.in_transaction() is True

        first_upload = Upload(_png_bytes(), f"media-boundary-{token}.png")
        first_asset = asyncio.run(
            media_api.upload_media(
                file=first_upload,
                idempotency_key=f"media-boundary-{token}-first",
                admin=request_admin,
                db=db,
            )
        )
        first_asset_id = int(first_asset.id)
        assert first_upload.closed is True
        assert transport.transaction_clean_checks == 1
        assert len(transport.calls) == 1
        assert transport.calls[0]["Bucket"] == "flashin-media-smoke"
        assert transport.calls[0]["Key"] == first_asset.storage_key

        # db.refresh() legitimately opens a fresh post-commit read transaction;
        # close it before verifying persisted state and the second request.
        db.rollback()
        persisted = db.query(MediaAsset).filter(MediaAsset.id == first_asset_id).one()
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.admin_id == admin_id,
                AuditLog.action == "media.upload",
                AuditLog.entity_type == "media_asset",
                AuditLog.entity_id == str(first_asset_id),
            )
            .one()
        )
        assert persisted.storage_key == first_asset.storage_key
        assert audit.entity_id == str(first_asset_id)
        first_count = db.query(MediaAsset).count()
        db.rollback()

        # A provider failure must happen outside PostgreSQL and must not create
        # a new MediaAsset or audit finalize record.
        request_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).one()
        assert db.in_transaction() is True
        transport.failure = TimeoutError("simulated object-store timeout")
        second_upload = Upload(_png_bytes(), f"media-boundary-timeout-{token}.png")
        ambiguous_storage_key = ""
        try:
            asyncio.run(
                media_api.upload_media(
                    file=second_upload,
                    idempotency_key=f"media-boundary-{token}-timeout",
                    admin=request_admin,
                    db=db,
                )
            )
        except media_storage.MediaStorageWriteError as exc:
            assert isinstance(exc.__cause__, TimeoutError)
            ambiguous_storage_key = exc.storage_key
        else:
            raise AssertionError("ambiguous storage write must propagate")

        assert second_upload.closed is True
        assert transport.transaction_clean_checks == 2
        assert db.in_transaction() is False
        assert db.query(MediaAsset).count() == first_count
        cleanup_row = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == MEDIA_CLEANUP_PROVIDER,
                ProviderCommand.idempotency_key
                == media_cleanup_idempotency_key(ambiguous_storage_key),
            )
            .one()
        )
        assert cleanup_row.status == "pending"
        assert ambiguous_storage_key in cleanup_row.payload_json
        cleanup_count_before_postcommit = (
            db.query(ProviderCommand)
            .filter(ProviderCommand.provider == MEDIA_CLEANUP_PROVIDER)
            .count()
        )
        db.rollback()

        # A response-side failure after commit must not compensate the provider
        # object. The same key must recover the committed row on retry without
        # another provider write.
        transport.failure = None
        media_api._committed_media_response = lambda _response: (_ for _ in ()).throw(
            RuntimeError("simulated post-commit response failure")
        )
        request_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).one()
        postcommit_key = f"media-boundary-{token}-postcommit"
        postcommit_upload = Upload(_png_bytes(), f"media-boundary-postcommit-{token}.png")
        provider_calls_before_postcommit = len(transport.calls)
        try:
            asyncio.run(
                media_api.upload_media(
                    file=postcommit_upload,
                    idempotency_key=postcommit_key,
                    admin=request_admin,
                    db=db,
                )
            )
        except RuntimeError as exc:
            assert "post-commit response failure" in str(exc)
        else:
            raise AssertionError("post-commit response failure must propagate")

        assert postcommit_upload.closed is True
        assert len(transport.calls) == provider_calls_before_postcommit + 1
        db.rollback()
        assert (
            db.query(ProviderCommand)
            .filter(ProviderCommand.provider == MEDIA_CLEANUP_PROVIDER)
            .count()
            == cleanup_count_before_postcommit
        )
        db.rollback()
        committed_after_failure = (
            db.query(MediaAsset)
            .filter(MediaAsset.upload_key == postcommit_key)
            .one()
        )
        committed_id = int(committed_after_failure.id)
        committed_storage_key = str(committed_after_failure.storage_key)
        db.rollback()

        media_api._committed_media_response = original_committed_response
        request_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).one()
        retry_upload = Upload(_png_bytes(), f"media-boundary-postcommit-{token}.png")
        provider_calls_before_retry = len(transport.calls)
        recovered = asyncio.run(
            media_api.upload_media(
                file=retry_upload,
                idempotency_key=postcommit_key,
                admin=request_admin,
                db=db,
            )
        )
        assert retry_upload.closed is True
        assert int(recovered.id) == committed_id
        assert str(recovered.storage_key) == committed_storage_key
        assert len(transport.calls) == provider_calls_before_retry
        db.rollback()

        print(
            json.dumps(
                {
                    "status": "ok",
                    "admin_id": admin_id,
                    "media_asset_id": first_asset_id,
                    "transaction_clean_checks": transport.transaction_clean_checks,
                    "provider_calls": len(transport.calls),
                    "successful_finalize_persisted": True,
                    "audit_persisted": True,
                    "timeout_finalize_created": False,
                    "ambiguous_write_cleanup_persisted": True,
                    "postcommit_cleanup_queued": False,
                    "postcommit_asset_recovered": True,
                    "retry_provider_write_created": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        media_storage.get_settings = original_get_settings
        media_storage._s3_client = original_s3_client
        media_api.generate_local_derivatives = original_generate_derivatives
        media_api._committed_media_response = original_committed_response
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
