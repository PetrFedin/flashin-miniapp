from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "backend" / "jobs" / "provider_command_jobs.py"
SAFETY = ROOT / "backend" / "services" / "provider_command_safety.py"


def test_provider_worker_reconciles_terminal_failures_before_claim_and_after_failure():
    source = WORKER.read_text(encoding="utf-8")

    assert "from ..services.provider_command_safety import" in source
    first_safety = source.index("enforce_terminal_provider_command_pilot_stop(db)")
    claim = source.index("claim_provider_commands(db")
    assert first_safety < claim
    assert source.count("enforce_terminal_provider_command_pilot_stop(db)") >= 3
    assert '_TERMINAL_FAILURE_STATES = {"failed", "review_required"}' in source


def test_provider_pilot_safety_scope_is_bounded_and_does_not_leak_provider_payloads():
    source = SAFETY.read_text(encoding="utf-8")

    assert '_PILOT_CRITICAL_PROVIDERS = ("moysklad",)' in source
    assert 'ProviderCommand.aggregate_type == "order"' in source
    assert "PilotOrderSlot.run_id == state.run_id" in source
    assert '"review_required": "provider_command_review_required"' in source
    assert '"failed": "provider_command_terminal_failed"' in source
    assert "payload_json" not in source
    assert "last_error" not in source
    assert "external_id" not in source
    assert "idempotency_key" not in source
