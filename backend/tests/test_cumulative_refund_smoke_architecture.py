from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts" / "cumulative_refund_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PAYMENTS = ROOT / "backend" / "services" / "payments.py"
REFUND_STATE = ROOT / "backend" / "services" / "refund_state.py"


def test_refund_smoke_uses_real_routes_and_transactional_postgres():
    source = SMOKE.read_text(encoding="utf-8")

    required_paths = (
        '"/api/cart/items"',
        '"/api/cart/promo"',
        '"/api/cart/loyalty"',
        '"/api/orders/checkout"',
        '"/api/payments"',
        '"/api/returns"',
        '"/api/returns/admin/approve"',
    )
    for path in required_paths:
        assert path in source

    assert 'join_transaction_mode="create_savepoint"' in source
    assert "outer_transaction.rollback()" in source
    assert "get_current_customer" in source
    assert "get_current_admin" in source
    assert "fake_create_yookassa_payment" in source
    assert "fake_create_yookassa_refund" in source


def test_refund_smoke_proves_partial_then_full_money_and_loyalty_state():
    source = SMOKE.read_text(encoding="utf-8")

    required_assertions = (
        'partial_order.status == "partially_refunded"',
        'partial_order.payment_status == "partially_refunded"',
        'Decimal("417.00")',
        'partial_hold.status == "committed"',
        'remaining_refundable_amount(db, partial_order) == Decimal("1000.00")',
        '"reject amount above remaining balance"',
        'persisted_order.status == "refunded"',
        'persisted_order.payment_status == "refunded"',
        "persisted_variant.stock_qty == 3",
        "persisted_variant.reserved_qty == 0",
        'persisted_hold.status == "refunded"',
        'Decimal("500.00")',
        'remaining_refundable_amount(db, persisted_order) == Decimal("0.00")',
        '("order_refund_reversal", Decimal("-17.00"))',
        '("loyalty_refund", Decimal("100.00"))',
        "len(refund_create_calls) == 2",
        "len(provider_refunds) == 2",
    )
    for fragment in required_assertions:
        assert fragment in source


def test_refund_smoke_proves_replay_does_not_repeat_provider_refund():
    source = SMOKE.read_text(encoding="utf-8")

    assert '"replay partial refund approval"' in source
    assert '"replay full cumulative refund approval"' in source
    assert 'first_refund_replay["idempotent"] is True' in source
    assert 'second_refund_replay["idempotent"] is True' in source
    assert source.count("assert len(refund_create_calls) == 1") == 2
    assert "assert len(refund_create_calls) == 2" in source


def test_provider_refund_key_and_cumulative_policy_remain_explicit():
    payments = PAYMENTS.read_text(encoding="utf-8")
    refund_state = REFUND_STATE.read_text(encoding="utf-8")

    for fragment in (
        "payment_id",
        "order_id",
        "refund_request_id",
        "normalized_amount",
        "currency",
    ):
        assert fragment in payments
    assert '"flashin:yookassa:refund:"' in payments
    assert "cumulative_total > order_total" in refund_state
    assert "cumulative_total == order_total" in refund_state
    assert "loyalty_adjusted_only_after_full_cumulative_refund" in refund_state


def test_ci_runs_cumulative_refund_smoke_before_full_backend_suite():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    review_position = workflow.index("Run transactional payment review smoke")
    refund_position = workflow.index("Run transactional cumulative refund smoke")
    tests_position = workflow.index("Run backend tests")

    assert review_position < refund_position < tests_position
    assert "python scripts/cumulative_refund_smoke.py" in workflow
