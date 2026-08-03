from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_payment_review_and_reconciliation_trip_pilot_runtime():
    payments = read("backend/api/payments.py")
    reconciliation = read("backend/services/payment_reconciliation.py")

    review = payments.index("def _queue_payment_review")
    emit = payments.index("emit_event", review)
    stop = payments.index("stop_pilot_for_order", review)
    assert stop < emit
    assert "trip_pilot_circuit_breaker" in payments
    assert "ProviderPaymentIntegrityError" in payments
    assert "payment_reconciliation_mismatch" in reconciliation
    assert "stop_pilot_for_order" in reconciliation


def test_payment_integrity_rolls_back_before_durable_trip():
    payments = read("backend/api/payments.py")
    create_except = payments.index("except ProviderPaymentIntegrityError as exc:")
    create_rollback = payments.index("db.rollback()", create_except)
    create_trip = payments.index("_trip_after_rollback", create_rollback)
    webhook_except = payments.index("except ProviderPaymentIntegrityError as exc:", create_except + 1)
    webhook_rollback = payments.index("db.rollback()", webhook_except)
    webhook_trip = payments.index("_trip_after_rollback", webhook_rollback)

    assert create_rollback < create_trip
    assert webhook_rollback < webhook_trip


def test_refund_retry_review_and_finalization_trip_pilot_runtime():
    returns = read("backend/api/returns.py")
    retry = returns.index("def _mark_retry_required")
    review = returns.index("def _mark_review_required")
    final = returns.index("def _trip_refund_after_rollback")

    assert "stop_pilot_for_order" in returns[retry:review]
    assert "stop_pilot_for_order" in returns[review:final]
    assert "trip_pilot_circuit_breaker" in returns[final:]
    assert "refund_finalization_integrity_failure" in returns
    assert "refund_finalization_integrity_conflict" in returns


def test_circuit_breaker_is_scoped_by_pilot_order_slot():
    service = read("backend/services/pilot_circuit_breaker.py")
    slot_query = service.index("PilotOrderSlot.order_id == order_id")
    no_slot = service.index("if slot is None", slot_query)
    state_lock = service.index("with_for_update()", no_slot)
    assert slot_query < no_slot < state_lock
    assert 'state.status in {"active", "completed"}' in service
    assert 'state.status = "stopped"' in service
    assert 'state.stop_reason = f"auto:{normalized_reason}"' in service
