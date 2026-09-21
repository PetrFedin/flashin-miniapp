#!/usr/bin/env python3
"""Prove durable media cleanup recovery on real PostgreSQL."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine
from backend.jobs import media_cleanup_jobs
from backend.models import MediaAsset
from backend.provider_models import ProviderCommand
from backend.services.media_cleanup import (
    MEDIA_CLEANUP_COMMAND,
    MEDIA_CLEANUP_PROVIDER,
    MediaCleanupReviewRequired,
    enqueue_media_cleanup,
    parse_media_cleanup_command,
    requeue_media_cleanup_command,
)
from backend.services.provider_commands import finish_provider_command


def _generated_key() -> str:
    return f"{uuid.uuid4().hex}.png"


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("media cleanup recovery smoke requires PostgreSQL")

    db = SessionLocal()
    created_command_ids: list[int] = []
    created_asset_ids: list[int] = []
    original_delete = media_cleanup_jobs.delete_media
    delete_calls: list[str] = []

    try:
        # 1) Durable enqueue is exact-key idempotent.
        key = _generated_key()
        first = enqueue_media_cleanup(db, key)
        db.refresh(first)
        created_command_ids.append(int(first.id))
        duplicate = enqueue_media_cleanup(db, key)
        assert int(duplicate.id) == int(first.id)
        assert (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == MEDIA_CLEANUP_PROVIDER,
                ProviderCommand.idempotency_key == first.idempotency_key,
            )
            .count()
            == 1
        )
        db.rollback()

        # 2) Claim commits before delete; fresh lease-fenced finalize succeeds.
        def fake_delete(storage_key: str) -> None:
            assert db.in_transaction() is False, "storage delete ran inside DB transaction"
            assert storage_key == key
            delete_calls.append(storage_key)

        media_cleanup_jobs.delete_media = fake_delete
        result = media_cleanup_jobs.process_media_cleanup_commands(db, limit=10)
        assert result["claimed"] >= 1
        assert result["deleted"] >= 1
        assert delete_calls == [key]

        db.expire_all()
        persisted = db.query(ProviderCommand).filter(ProviderCommand.id == first.id).one()
        assert persisted.status == "sent"
        assert persisted.lease_token is None
        assert persisted.external_id.startswith("deleted:")
        assert key not in persisted.external_id
        db.rollback()

        # 3) A stale worker token cannot finalize completed work.
        assert finish_provider_command(
            db,
            int(first.id),
            "stale-worker-token",
            external_id="deleted:stale",
        ) is False

        # 4) Retryable provider failure schedules bounded retry and can later recover.
        retry_key = _generated_key()
        retry_command = enqueue_media_cleanup(db, retry_key)
        db.refresh(retry_command)
        created_command_ids.append(int(retry_command.id))

        def timeout_delete(storage_key: str) -> None:
            assert db.in_transaction() is False
            assert storage_key == retry_key
            raise TimeoutError("simulated object storage timeout")

        media_cleanup_jobs.delete_media = timeout_delete
        retry_result = media_cleanup_jobs.process_media_cleanup_commands(db, limit=10)
        assert retry_result["retry_scheduled"] >= 1
        db.expire_all()
        retry_row = db.query(ProviderCommand).filter(
            ProviderCommand.id == retry_command.id
        ).one()
        assert retry_row.status == "pending"
        assert int(retry_row.attempts) == 1
        db.rollback()

        media_cleanup_jobs.delete_media = lambda storage_key: delete_calls.append(storage_key)
        retry_row.next_attempt_at = None
        db.commit()
        recovered_result = media_cleanup_jobs.process_media_cleanup_commands(db, limit=10)
        assert recovered_result["deleted"] >= 1
        db.expire_all()
        retry_row = db.query(ProviderCommand).filter(
            ProviderCommand.id == retry_command.id
        ).one()
        assert retry_row.status == "sent"
        db.rollback()

        # 5) Malformed/wrong-type commands fail closed before provider delete.
        malformed = ProviderCommand(
            provider=MEDIA_CLEANUP_PROVIDER,
            command_type="object_storage.media.put",
            idempotency_key=f"media-cleanup-bad:{uuid.uuid4().hex}",
            aggregate_type="media_cleanup",
            aggregate_id=uuid.uuid4().hex,
            payload_json=(
                '{"reason":"upload_finalize_failed","storage_key":"'
                + _generated_key()
                + '"}'
            ),
            status="pending",
        )
        db.add(malformed)
        db.commit()
        db.refresh(malformed)
        created_command_ids.append(int(malformed.id))

        before_malformed_delete_count = len(delete_calls)
        malformed_result = media_cleanup_jobs.process_media_cleanup_commands(db, limit=10)
        assert malformed_result["review_required"] >= 1
        assert len(delete_calls) == before_malformed_delete_count
        db.expire_all()
        malformed = db.query(ProviderCommand).filter(
            ProviderCommand.id == malformed.id
        ).one()
        assert malformed.status == "review_required"

        try:
            requeue_media_cleanup_command(db, int(malformed.id))
        except MediaCleanupReviewRequired:
            db.rollback()
        else:
            raise AssertionError("malformed cleanup command unexpectedly requeued")

        # 6) Manual replay is command-id-only and preserves immutable key binding.
        replay_key = _generated_key()
        replay = enqueue_media_cleanup(db, replay_key)
        db.refresh(replay)
        created_command_ids.append(int(replay.id))
        original_payload = replay.payload_json
        original_aggregate = replay.aggregate_id
        replay.status = "failed"
        replay.next_attempt_at = None
        db.commit()

        replayed, previous_status = requeue_media_cleanup_command(db, int(replay.id))
        assert previous_status == "failed"
        assert replayed.status == "pending"
        assert replayed.payload_json == original_payload
        assert replayed.aggregate_id == original_aggregate
        db.commit()
        db.refresh(replayed)
        assert parse_media_cleanup_command(
            {
                "provider": replayed.provider,
                "command_type": replayed.command_type,
                "idempotency_key": replayed.idempotency_key,
                "aggregate_type": replayed.aggregate_type,
                "aggregate_id": replayed.aggregate_id,
                "payload_json": replayed.payload_json,
            }
        ) == replay_key
        # Keep this row out of the next worker claim.
        replayed.status = "failed"
        replayed.next_attempt_at = None
        db.commit()

        # 7) Even a valid cleanup command may never delete a committed MediaAsset.
        referenced_key = _generated_key()
        asset = MediaAsset(
            upload_key=f"cleanup-smoke-{uuid.uuid4().hex}",
            url=f"https://cdn.flashin.test/{referenced_key}",
            storage_key=referenced_key,
            filename="referenced.png",
            content_type="image/png",
            size_bytes=42,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        created_asset_ids.append(int(asset.id))

        referenced_command = enqueue_media_cleanup(db, referenced_key)
        db.refresh(referenced_command)
        created_command_ids.append(int(referenced_command.id))
        before_reference_delete_count = len(delete_calls)
        referenced_result = media_cleanup_jobs.process_media_cleanup_commands(db, limit=10)
        assert referenced_result["review_required"] >= 1
        assert len(delete_calls) == before_reference_delete_count
        db.expire_all()
        referenced_command = db.query(ProviderCommand).filter(
            ProviderCommand.id == referenced_command.id
        ).one()
        assert referenced_command.status == "review_required"
        assert "referenced by MediaAsset" in referenced_command.last_error
        db.rollback()

        print(
            {
                "status": "ok",
                "provider": MEDIA_CLEANUP_PROVIDER,
                "command_type": MEDIA_CLEANUP_COMMAND,
                "transaction_clean_delete": True,
                "duplicate_enqueue_idempotent": True,
                "retry_scheduled_and_recovered": True,
                "stale_lease_rejected": True,
                "malformed_command_review_required": True,
                "manual_replay_preserves_storage_key": True,
                "authoritative_media_reference_blocks_delete": True,
            }
        )
        return 0
    finally:
        media_cleanup_jobs.delete_media = original_delete
        db.rollback()
        if created_command_ids:
            db.query(ProviderCommand).filter(
                ProviderCommand.id.in_(created_command_ids)
            ).delete(synchronize_session=False)
            db.commit()
        if created_asset_ids:
            db.query(MediaAsset).filter(
                MediaAsset.id.in_(created_asset_ids)
            ).delete(synchronize_session=False)
            db.commit()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
