from pathlib import Path

from backend.checkout_models import CheckoutAttempt


ROOT = Path(__file__).resolve().parents[2]


def test_checkout_attempt_has_required_unique_constraints():
    constraints = {constraint.name for constraint in CheckoutAttempt.__table__.constraints}

    assert "uq_checkout_attempt_customer_key" in constraints
    assert "uq_checkout_attempt_cart" in constraints
    assert "uq_checkout_attempt_order" in constraints


def test_checkout_migration_is_connected_to_current_head():
    migration = ROOT / "backend" / "alembic" / "versions" / "0015_checkout_idempotency.py"
    source = migration.read_text(encoding="utf-8")

    assert 'revision = "0015_checkout_idempotency"' in source
    assert 'down_revision = "0014_multiple_partial_refunds"' in source
    assert '"checkout_attempts"' in source
    assert '"uq_checkout_attempt_customer_key"' in source


def test_runtime_and_alembic_register_checkout_model():
    main_source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    env_source = (ROOT / "backend" / "alembic" / "env.py").read_text(encoding="utf-8")

    assert "checkout_models as _checkout_models" in main_source
    assert "from backend import checkout_models" in env_source


def test_frontend_persists_and_sends_checkout_key():
    source = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")

    assert 'const CHECKOUT_KEY_PREFIX = "flashin_checkout_key:"' in source
    assert 'headers: { "Idempotency-Key": idempotencyKey }' in source
    assert "localStorage.removeItem(storageKey)" in source
    assert source.index('headers: { "Idempotency-Key": idempotencyKey }') < source.index(
        "localStorage.removeItem(storageKey)"
    )
