from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts" / "payment_review_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DISPATCHER = ROOT / "backend" / "services" / "event_dispatcher.py"
REVIEW_SERVICE = ROOT / "backend" / "services" / "payment_review.py"


def test_payment_review_event_is_persisted_in_the_emitting_transaction():
    dispatcher = DISPATCHER.read_text(encoding="utf-8")
    review_service = REVIEW_SERVICE.read_text(encoding="utf-8")

    handler_call = "_apply_domain_handler(db, event_type, normalized_payload)"
    assert '"payment.review_required": ensure_payment_review_case' in dispatcher
    assert handler_call in dispatcher
    assert "db.add(event)" in dispatcher
    assert dispatcher.index(handler_call) < dispatcher.index("db.add(event)")

    assert "PaymentReconciliation(" in review_service
    assert 'Payment.provider == provider' in review_service
    assert 'PaymentReconciliation.status == "open"' in review_service
    assert "PaymentReconciliation.payment_id == payment.id" in review_service
    assert "PaymentReconciliation.message == message" in review_service


def test_late_success_smoke_uses_real_http_and_transactional_postgres():
    source = SMOKE.read_text(encoding="utf-8")

    required_paths = (
        '"/api/cart/items"',
        '"/api/cart/promo"',
        '"/api/cart/loyalty"',
        '"/api/orders/checkout"',
        '"/api/payments"',
        '"/api/payments/webhook/yookassa"',
    )
    for path in required_paths:
        assert path in source

    assert 'join_transaction_mode="create_savepoint"' in source
    assert "outer_transaction.rollback()" in source
    assert '"payment.canceled"' in source
    assert '"payment.succeeded"' in source
    assert '"idempotent cancellation webhook replay"' in source
    assert '"idempotent late success webhook replay"' in source


def test_late_success_smoke_proves_review_without_accidental_settlement():
    source = SMOKE.read_text(encoding="utf-8")

    required_assertions = (
        'persisted_order.status == "payment_review_required"',
        'persisted_order.payment_status == "paid_review_required"',
        'persisted_order.delivery_status == "cancelled"',
        'persisted_payment.status == "succeeded"',
        "persisted_variant.stock_qty == 5",
        "persisted_variant.reserved_qty == 0",
        "persisted_promo.used_count == 0",
        'persisted_hold.status == "released"',
        'reconciliation.status == "open"',
        'reconciliation.message == "payment.review_required:paid_after_cancel"',
        "len(reconciliation_rows) == 1",
        "len(review_events) == 1",
        "len(payment_events) == 2",
        "len(notifications) == 1",
        "len(paid_keys) == 0",
        "db.query(FulfillmentTask)",
        "db.query(LoyaltyTransaction)",
    )
    for fragment in required_assertions:
        assert fragment in source


def test_ci_runs_payment_review_smoke_before_full_backend_suite():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    cancellation_position = workflow.index(
        "Run transactional order cancellation smoke"
    )
    review_position = workflow.index("Run transactional payment review smoke")
    tests_position = workflow.index("Run backend tests")

    assert cancellation_position < review_position < tests_position
    assert "python scripts/payment_review_smoke.py" in workflow
