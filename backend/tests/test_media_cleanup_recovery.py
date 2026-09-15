from backend.jobs import media_cleanup_jobs
from backend.services.media_cleanup import (
    MEDIA_CLEANUP_AGGREGATE,
    MEDIA_CLEANUP_COMMAND,
    MEDIA_CLEANUP_PROVIDER,
    MediaCleanupReviewRequired,
    media_cleanup_digest,
    media_cleanup_idempotency_key,
    parse_media_cleanup_command,
    validate_generated_media_storage_key,
)


def _command(storage_key: str) -> dict:
    digest = media_cleanup_digest(storage_key)
    return {
        "id": 41,
        "provider": MEDIA_CLEANUP_PROVIDER,
        "command_type": MEDIA_CLEANUP_COMMAND,
        "idempotency_key": media_cleanup_idempotency_key(storage_key),
        "aggregate_type": MEDIA_CLEANUP_AGGREGATE,
        "aggregate_id": digest,
        "payload_json": f'{{"reason":"upload_finalize_failed","storage_key":"{storage_key}"}}',
        "lease_token": "lease-41",
    }


def test_generated_media_key_allowlist_rejects_arbitrary_paths():
    assert validate_generated_media_storage_key("a" * 32 + ".png").endswith(".png")
    for value in ("", "object.png", "../object.png", "a" * 32 + ".svg"):
        try:
            validate_generated_media_storage_key(value)
        except MediaCleanupReviewRequired:
            pass
        else:
            raise AssertionError(f"unexpected cleanup key accepted: {value}")


def test_payload_binding_rejects_changed_aggregate():
    command = _command("b" * 32 + ".webp")
    command["aggregate_id"] = "0" * 64
    try:
        parse_media_cleanup_command(command)
    except MediaCleanupReviewRequired as exc:
        assert "binding" in str(exc)
    else:
        raise AssertionError("changed aggregate binding must fail closed")


def test_worker_deletes_only_after_claim_commit(monkeypatch):
    key = "c" * 32 + ".jpg"
    command = _command(key)

    class Db:
        def in_transaction(self):
            return False

    deleted = []
    finished = []
    monkeypatch.setattr(media_cleanup_jobs, "claim_provider_commands", lambda db, *, provider, limit: [command])
    monkeypatch.setattr(media_cleanup_jobs, "delete_media", lambda storage_key: deleted.append(storage_key))
    monkeypatch.setattr(
        media_cleanup_jobs,
        "finish_provider_command",
        lambda db, command_id, lease_token, *, external_id: finished.append(
            (command_id, lease_token, external_id)
        ) or True,
    )

    result = media_cleanup_jobs.process_media_cleanup_commands(Db())

    assert deleted == [key]
    assert result["deleted"] == 1
    assert finished[0][0:2] == (41, "lease-41")
    assert key not in finished[0][2]


def test_malformed_command_enters_review_before_delete(monkeypatch):
    command = _command("d" * 32 + ".png")
    command["command_type"] = "object_storage.media.put"

    class Db:
        def in_transaction(self):
            return False

    failures = []
    monkeypatch.setattr(media_cleanup_jobs, "claim_provider_commands", lambda db, *, provider, limit: [command])
    monkeypatch.setattr(
        media_cleanup_jobs,
        "delete_media",
        lambda _storage_key: (_ for _ in ()).throw(AssertionError("malformed command must not delete")),
    )
    monkeypatch.setattr(
        media_cleanup_jobs,
        "fail_provider_command",
        lambda db, command_id, lease_token, exc, review_required=False: failures.append(
            (command_id, review_required, str(exc))
        ) or "review_required",
    )

    result = media_cleanup_jobs.process_media_cleanup_commands(Db())

    assert result["review_required"] == 1
    assert failures[0][0:2] == (41, True)


def test_provider_delete_failure_uses_bounded_retry_state(monkeypatch):
    command = _command("e" * 32 + ".png")

    class Db:
        def in_transaction(self):
            return False

    monkeypatch.setattr(media_cleanup_jobs, "claim_provider_commands", lambda db, *, provider, limit: [command])
    monkeypatch.setattr(
        media_cleanup_jobs,
        "delete_media",
        lambda _storage_key: (_ for _ in ()).throw(TimeoutError("storage timeout")),
    )
    monkeypatch.setattr(
        media_cleanup_jobs,
        "fail_provider_command",
        lambda db, command_id, lease_token, exc, review_required=False: "retry_scheduled",
    )

    result = media_cleanup_jobs.process_media_cleanup_commands(Db())

    assert result["retry_scheduled"] == 1
    assert result["deleted"] == 0
