from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts" / "order_cancellation_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_cancellation_smoke_uses_real_http_and_transactional_postgres():
    source = SMOKE.read_text(encoding="utf-8")

    assert 'join_transaction_mode="create_savepoint"' in source
    assert "outer_transaction.rollback()" in source
    assert 'client.post("/api/cart/items"' in source
    assert 'client.post("/api/cart/promo"' in source
    assert 'client.post("/api/cart/loyalty"' in source
    assert '"/api/orders/checkout"' in source
    assert 'client.post(f"/api/orders/{order_id}/cancel")' in source
    assert '"idempotent cancellation replay"' in source
    assert "create_yookassa_payment" not in source


def test_cancellation_smoke_proves_every_reservation_is_released_once():
    source = SMOKE.read_text(encoding="utf-8")

    required_assertions = (
        "persisted_variant.stock_qty == 5",
        "persisted_variant.reserved_qty == 0",
        "persisted_promo.used_count == 0",
        'persisted_hold.status == "released"',
        "persisted_hold.released_at is not None",
        'Decimal("500.00")',
        "db.query(Payment).filter(Payment.order_id == order_id).count() == 0",
        "db.query(LoyaltyTransaction).filter(LoyaltyTransaction.order_id == order_id).count() == 0",
        "len(notifications) == 1",
        "len(event_keys) == 1",
    )
    for fragment in required_assertions:
        assert fragment in source


def test_ci_runs_cancellation_smoke_after_paid_journey_and_before_unit_tests():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    paid_smoke_position = workflow.index("Run transactional customer journey smoke")
    cancellation_position = workflow.index("Run transactional order cancellation smoke")
    tests_position = workflow.index("Run backend tests")

    assert paid_smoke_position < cancellation_position < tests_position
    assert "python scripts/order_cancellation_smoke.py" in workflow
