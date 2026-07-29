def test_order_statuses_defined():
    from backend.api.admin import _ORDER_TRANSITIONS

    statuses = set(_ORDER_TRANSITIONS)
    statuses.update(
        target
        for targets in _ORDER_TRANSITIONS.values()
        for target in targets
    )

    assert "paid" in statuses
    assert "refund_requested" in statuses
    assert "cancelled" in statuses
    assert "refunded" in statuses
