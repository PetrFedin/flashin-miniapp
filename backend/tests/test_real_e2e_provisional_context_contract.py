from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "backend/tests/e2e/test_real_order_flow_runner.py"
TERMINAL = ROOT / "backend/tests/e2e/test_order_payment_refund_flow.py"
ADMISSION = ROOT / "scripts/pilot_lifecycle_admission.py"


def test_real_payment_intent_is_durable_before_checkout_and_payment():
    source = RUNNER.read_text(encoding="utf-8")

    intent = source.index('"phase": "checkout_intent"')
    checkout = source.index('f"{API}/api/orders/checkout"')
    order_created = source.index('"phase": "order_created"')
    payment = source.index('f"{API}/api/payments"')
    payment_created = source.index('"phase": "payment_created"')

    assert intent < checkout < order_created < payment < payment_created
    assert "os.fsync(handle.fileno())" in source
    assert "os.fsync(directory_fd)" in source
    assert '"checkout_idempotency_key": idempotency_key' in source
    assert "assert not CONTEXT_FILE.exists()" in source


def test_terminal_verifier_rejects_provisional_real_order_context():
    source = TERMINAL.read_text(encoding="utf-8")

    assert 'context.get("phase") == "payment_created"' in source
    assert "Real E2E context is provisional" in source


def test_admission_rejects_provisional_real_order_context():
    source = ADMISSION.read_text(encoding="utf-8")

    assert 'context.get("phase") != "payment_created"' in source
    assert "real-order E2E context is provisional and has not reached payment_created" in source
