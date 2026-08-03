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


def test_deploy_and_rollback_stop_active_pilot_and_sign_release_capability():
    deploy = read("scripts/deploy_production.sh")
    rollback = read("scripts/rollback.sh")
    marker = "pilot_runtime.py _stop"
    capability = "pilot_release_capability.py stamp --slot current"
    inspect = "pilot_release_capability.py\" inspect --archive"

    assert marker in deploy
    assert deploy.index(marker) < deploy.index("readiness_gate.py --phase predeploy")
    assert capability in deploy
    assert deploy.index(capability) > deploy.index("release_control.py promote")

    assert inspect in rollback
    assert rollback.index(inspect) < rollback.index("docker compose down")
    first_stop = rollback.index(marker)
    restored_stop = rollback.index(marker, first_stop + 1)
    assert first_stop < rollback.index("docker compose down")
    assert "rollback database restored" in rollback[restored_stop:]
    assert restored_stop < rollback.index("Starting rolled-back production services")
    assert capability in rollback
    assert rollback.index(capability) > rollback.index("release_control.py\" promote")


def test_release_capability_requires_runtime_checkout_and_safe_operations():
    source = read("scripts/pilot_release_capability.py")
    for required in (
        "backend/pilot_models.py",
        "backend/services/pilot_runtime.py",
        "backend/alembic/versions/0022_pilot_runtime_guard.py",
        "backend/api/orders.py",
        "docker-compose.production.yml",
        "scripts/deploy_production.sh",
        "scripts/rollback.sh",
    ):
        assert required in source
    for marker in (
        "acquire_pilot_checkout(",
        "record_pilot_order(",
        "./docs:/app/docs:ro",
        "./deploy/release:/app/deploy/release:ro",
        "pilot_runtime.py _stop",
        "pilot_release_capability.py inspect --archive",
    ):
        assert marker in source


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
