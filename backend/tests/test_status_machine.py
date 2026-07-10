def test_order_statuses_defined():
    from backend.api.orders import ORDER_STATUSES
    assert "paid" in ORDER_STATUSES
    assert "refund_requested" in ORDER_STATUSES
    assert "cancelled" in ORDER_STATUSES
