from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts" / "customer_journey_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_smoke_script_exercises_the_money_path_through_http():
    source = SMOKE.read_text(encoding="utf-8")

    required_requests = (
        '"/api/cart/items"',
        '"/api/cart/promo"',
        '"/api/cart/loyalty"',
        '"/api/orders/checkout"',
        '"/api/payments"',
        '"/api/payments/webhook/yookassa"',
        '"idempotent checkout replay"',
        '"idempotent payment webhook replay"',
    )
    for fragment in required_requests:
        assert fragment in source


def test_smoke_script_verifies_persisted_financial_invariants():
    source = SMOKE.read_text(encoding="utf-8")

    required_models = (
        "ProductVariant",
        "PromoCode",
        "LoyaltyRedemptionHold",
        "LoyaltyTransaction",
        "FulfillmentTask",
        "NotificationEventKey",
        "PaymentEvent",
        "CheckoutAttempt",
    )
    for model in required_models:
        assert model in source

    assert 'join_transaction_mode="create_savepoint"' in source
    assert "outer_transaction.rollback()" in source
    assert 'persisted_order.status == "paid"' in source
    assert "persisted_variant.stock_qty == 3" in source
    assert "persisted_variant.reserved_qty == 0" in source
    assert "persisted_promo.used_count == 1" in source
    assert "len(notifications) == 1" in source
    assert "len(event_keys) == 1" in source


def test_ci_runs_smoke_after_migrations_and_before_unit_tests():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    migration_position = workflow.index("Apply database migrations")
    smoke_position = workflow.index("Run transactional customer journey smoke")
    tests_position = workflow.index("Run backend tests")

    assert migration_position < smoke_position < tests_position
    assert "python scripts/customer_journey_smoke.py" in workflow
