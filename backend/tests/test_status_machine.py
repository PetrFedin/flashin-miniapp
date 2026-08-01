from backend.order_statuses import ORDER_STATUSES


def test_order_statuses_defined():
    assert "paid" in ORDER_STATUSES
    assert "refund_requested" in ORDER_STATUSES
    assert "cancelled" in ORDER_STATUSES


def test_payment_only_statuses_do_not_leak_into_order_domain():
    assert "refund_pending" not in ORDER_STATUSES
    assert "pending" not in ORDER_STATUSES
