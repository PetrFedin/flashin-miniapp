from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_checkout_keeps_idempotent_retries_outside_new_slot_and_records_atomically():
    source = read("backend/api/orders.py")
    existing = source.index("if existing_order:")
    guard = source.index("acquire_pilot_checkout(")
    cart = source.index("_load_locked_active_cart", guard)
    record = source.index("record_pilot_order(")
    reserve = source.index("for cart_item in sorted", record)
    assert existing < guard < cart
    assert record < reserve
    assert "db.commit()" in source[record:]


def test_runtime_state_is_migrated_and_private_evidence_is_read_only():
    migration = read("backend/alembic/versions/0022_pilot_runtime_guard.py")
    compose = read("docker-compose.production.yml")
    assert 'down_revision = "0021_business_event_recovery_states"' in migration
    assert "pilot_runtime_state" in migration
    assert "pilot_order_slots" in migration
    assert "./docs:/app/docs:ro" in compose
    assert "./deploy/release:/app/deploy/release:ro" in compose


def test_deploy_and_rollback_stop_active_pilot_before_code_changes():
    deploy = read("scripts/deploy_production.sh")
    rollback = read("scripts/rollback.sh")
    marker = "pilot_runtime.py _stop"
    assert marker in deploy
    assert deploy.index(marker) < deploy.index("readiness_gate.py --phase predeploy")
    assert marker in rollback
    assert rollback.index(marker) < rollback.index("docker compose down")


def test_production_environment_requires_exact_twenty_order_guard():
    example = read(".env.production.example")
    validator = read("scripts/validate_env.py")
    assert "PILOT_RUNTIME_ENFORCED=true" in example
    assert "PILOT_RUNTIME_MAX_ORDERS=20" in example
    assert "PILOT_RUNTIME_ENFORCED must be true in production" in validator
    assert "PILOT_RUNTIME_MAX_ORDERS must equal 20 in production" in validator


def test_runtime_make_targets_are_unique():
    makefile = read("Makefile")
    for target in ("pilot-runtime-arm:", "pilot-runtime-status:", "pilot-runtime-stop:"):
        assert makefile.count(target) == 1
    for existing in ("down:", "logs:", "migrate:", "health:", "workers:"):
        assert makefile.count(existing) == 1
