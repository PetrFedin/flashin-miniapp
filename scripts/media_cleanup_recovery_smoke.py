#!/usr/bin/env python3
"""Prove durable media cleanup claims before provider I/O and finalizes fresh."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal
from backend.jobs import media_cleanup_jobs
from backend.provider_models import ProviderCommand
from backend.services.media_cleanup import (
    MEDIA_CLEANUP_COMMAND,
    MEDIA_CLEANUP_PROVIDER,
    enqueue_media_cleanup,
    requeue_media_cleanup_command,
)
from backend.services.provider_commands import finish_provider_command


def main() -> int:
    token = uuid.uuid4().hex
    key = f"{token}.png"
    db = SessionLocal()
    created_ids: list[int] = []
    original_delete = media_cleanup_jobs.delete_media
    delete_calls: list[str] = []

    try:
        first = enqueue_media_cleanup(db, key)
        db.refresh(first)
        created_ids.append(int(first.id))
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

        def fake_delete(storage_key: str) -> None:
            assert db.in_transaction() is False, "storage delete ran inside DB transaction"
            assert storage_key == key
            delete_calls.append(storage_key)

        media_cleanup_jobs.delete_media = fake_delete
        result = media_cleanup_jobs.process_media_cleanup_commands(db, limit=10)
        assert result["claimed"] == 1
        assert result["deleted"] == 1
        assert delete_calls == [key]

        db.expire_all()
        persisted = db.query(ProviderCommand).filter(ProviderCommand.id == first.id).one()
        assert persisted.status == "sent"
        assert persisted.lease_token is None
        assert persisted.external_id.startswith("deleted:")
        assert key not in persisted.external_id

        # A stale worker token cannot finalize the completed command.
        assert finish_provider_command(
            db,
            int(first.id),
            "stale-worker-token",
            external_id="deleted:stale",
        ) is False

        malformed = ProviderCommand(
            provider=MEDIA_CLEANUP_PROVIDER,
            command_type="object_storage.media.put",
            idempotency_key=f"media-cleanup-bad:{uuid.uuid4().hex}",
            aggregate_type="media_cleanup",
            aggregate_id=uuid.uuid4().hex,
            payload_json='{"reason":"upload_finalize_failed","storage_key":"' + key + '"}',
            status="pending",
        )
        db.add(malformed)
        db.commit()
        db.refresh(malformed)
        created_ids.append(int(malformed.id))

        delete_calls.clear()
        malformed_result = media_cleanup_jobs.process_media_cleanup_commands(db, limit=10)
        assert malformed_result["review_required"] == 1
        assert delete_calls == []
        db.expire_all()
        malformed = db.query(ProviderCommand).filter(ProviderCommand.id == malformed.id).one()
        assert malformed.status == "review_required"

        original_payload = malformed.payload_json
        requeued, previous_status = requeue_media_cleanup_command(db, int(malformed.id))
        # Requeue validates the binding and therefore must reject this malformed
        # command instead of letting an operator redirect deletion.
        raise AssertionError(
            f"malformed command unexpectedly requeued: {requeued.id} from {previous_status}; {original_payload}"
        )
    except Exception as exc:
        # The malformed command is expected to be non-requeueable because its
        # command type is not the allowlisted delete type. Distinguish that
        # expected safety result from all other smoke failures.
        if not (
            isinstance(exc, ValueError)
            and "media cleanup command" in str(exc).lower()
            and created_ids
        ):
            raise

        print(
            {
                "status": "ok",
                "cleanup_command_id": created_ids[0],
                "provider": MEDIA_CLEANUP_PROVIDER,
                "command_type": MEDIA_CLEANUP_COMMAND,
                "transaction_clean_delete": True,
                "duplicate_enqueue_idempotent": True,
                "malformed_command_review_required": True,
                "manual_replay_preserves_allowlist": True,
            }
        )
        return 0
    finally:
        media_cleanup_jobs.delete_media = original_delete
        db.rollback()
        if created_ids:
            db.query(ProviderCommand).filter(ProviderCommand.id.in_(created_ids)).delete(
                synchronize_session=False
            )
            db.commit()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
