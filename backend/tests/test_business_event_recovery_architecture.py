from pathlib import Path

import pytest

from backend.services import event_dispatcher

ROOT = Path(__file__).resolve().parents[2]


def test_payload_helpers_reject_non_objects_and_non_finite_numbers():
    with pytest.raises(event_dispatcher.BusinessEventPayloadError):
        event_dispatcher._parse_payload("[]")
    with pytest.raises(event_dispatcher.BusinessEventPayloadError):
        event_dispatcher._parse_payload("not-json")
    with pytest.raises(event_dispatcher.BusinessEventPayloadError):
        event_dispatcher._parse_payload('{"invalid": NaN}')
    with pytest.raises(event_dispatcher.BusinessEventPayloadError):
        event_dispatcher._serialize_payload({"invalid": float("nan")})


def test_failure_diagnostics_are_bounded():
    error = RuntimeError("x" * 5000)

    rendered = event_dispatcher._format_failure(error)

    assert rendered.startswith("RuntimeError: ")
    assert len(rendered) == event_dispatcher._MAX_ERROR_LENGTH


def test_replay_contract_is_locked_and_failed_only():
    source = (ROOT / "backend" / "services" / "event_dispatcher.py").read_text(
        encoding="utf-8"
    )

    assert ".with_for_update()" in source
    assert 'if event.status != "failed"' in source
    assert 'event.status = "pending"' in source
    assert "event.attempts = 0" in source
    assert "recovery.replay_count" in source
    assert "replacement_payload" in source


def test_admin_replay_is_audited_and_not_processed_inline():
    source = (ROOT / "backend" / "api" / "platform.py").read_text(encoding="utf-8")

    assert '@router.post("/admin/events/{event_id}/replay")' in source
    assert '"business_event.replay"' in source
    assert 'require_permission(db, admin, "orders.write")' in source
    assert "process_pending_events" not in source
    assert '@router.get("/admin/events/summary")' in source


def test_recovery_migration_has_single_chain_and_foreign_keys():
    migration = (
        ROOT
        / "backend"
        / "alembic"
        / "versions"
        / "0021_business_event_recovery_states.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "0020_notification_delivery_lease_tokens"' in migration
    assert '"business_event_recovery_states"' in migration
    assert '["business_events.id"]' in migration
    assert '["admin_users.id"]' in migration
    assert 'server_default="0"' in migration
