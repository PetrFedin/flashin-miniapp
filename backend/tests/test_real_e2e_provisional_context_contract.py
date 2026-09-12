from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "backend/tests/e2e/test_real_order_flow_runner.py"
TERMINAL = ROOT / "backend/tests/e2e/test_order_payment_refund_flow.py"
ADMISSION = ROOT / "scripts/pilot_lifecycle_admission.py"


def test_real_payment_slot_is_atomically_claimed_before_any_mutation():
    source = RUNNER.read_text(encoding="utf-8")

    preflight = source.index('"phase": "preflight_intent"')
    cart = source.index('f"{API}/api/cart/items"')
    checkout_intent = source.index('"phase": "checkout_intent"')
    checkout = source.index('f"{API}/api/orders/checkout"')
    order_created = source.index('"phase": "order_created"')
    payment = source.index('f"{API}/api/payments"')
    payment_created = source.index('"phase": "payment_created"')

    assert preflight < cart < checkout_intent < checkout < order_created < payment < payment_created
    assert "os.O_EXCL" in source
    assert "0o600" in source
    assert "os.fsync(handle.fileno())" in source
    assert "os.fsync(directory_fd)" in source
    assert '"checkout_idempotency_key": idempotency_key' in source


def test_terminal_verifier_rejects_provisional_real_order_context():
    source = TERMINAL.read_text(encoding="utf-8")

    assert 'context.get("phase") == "payment_created"' in source
    assert "Real E2E context is provisional" in source


def test_admission_rejects_provisional_real_order_context():
    source = ADMISSION.read_text(encoding="utf-8")

    assert 'context.get("phase") != "payment_created"' in source
    assert "real-order E2E context is provisional and has not reached payment_created" in source
