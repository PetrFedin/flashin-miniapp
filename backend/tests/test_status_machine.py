from backend.order_statuses import (
    ADMIN_MANAGED_ORDER_TRANSITIONS,
    ORDER_STATUSES,
    PROVIDER_OWNED_ORDER_STATUSES,
)


def test_order_statuses_defined():
    assert "paid" in ORDER_STATUSES
    assert "refund_requested" in ORDER_STATUSES
    assert "cancelled" in ORDER_STATUSES


def test_payment_only_statuses_do_not_leak_into_order_domain():
    assert "refund_pending" not in ORDER_STATUSES
    assert "pending" not in ORDER_STATUSES


def test_generic_admin_transitions_cover_only_fulfillment_progress():
    assert ADMIN_MANAGED_ORDER_TRANSITIONS == {
        "paid": frozenset({"assembling"}),
        "assembling": frozenset({"ready"}),
        "ready": frozenset({"shipped"}),
        "shipped": frozenset({"completed"}),
    }


def test_provider_owned_statuses_cannot_be_admin_transition_targets():
    admin_targets = set().union(*ADMIN_MANAGED_ORDER_TRANSITIONS.values())

    assert admin_targets.isdisjoint(PROVIDER_OWNED_ORDER_STATUSES)
    assert PROVIDER_OWNED_ORDER_STATUSES <= ORDER_STATUSES
