from backend.order_statuses import ORDER_STATUSES
from backend.services.inventory_movement_contract import expected_core_chain


def test_every_canonical_order_status_has_explicit_inventory_semantics():
    expected = {
        "created": ("reserve",),
        "payment_created": ("reserve",),
        "payment_review_required": ("reserve", "release"),
        "paid": ("reserve", "commit"),
        "assembling": ("reserve", "commit"),
        "ready": ("reserve", "commit"),
        "shipped": ("reserve", "commit"),
        "completed": ("reserve", "commit"),
        "refund_requested": ("reserve", "commit"),
        "partially_refunded": ("reserve", "commit"),
        "refunded": ("reserve", "commit"),
        "cancelled": ("reserve", "release"),
    }

    assert set(expected) == set(ORDER_STATUSES)
    assert {status: expected_core_chain(status) for status in ORDER_STATUSES} == expected


def test_financial_refund_status_never_synthesizes_physical_return():
    assert expected_core_chain("refund_requested") == ("reserve", "commit")
    assert expected_core_chain("partially_refunded") == ("reserve", "commit")
    assert expected_core_chain("refunded") == ("reserve", "commit")


def test_payment_review_releases_reservation_without_sale_commit():
    assert expected_core_chain("payment_review_required") == ("reserve", "release")
