from pathlib import Path

from fastapi.routing import APIRoute

from backend.api import admin as admin_api
from backend.api import order_cancellation


ROOT = Path(__file__).resolve().parents[1]


def test_monolith_no_longer_exposes_or_contains_generic_order_mutation():
    source = (ROOT / "api" / "admin.py").read_text(encoding="utf-8")
    registered = [
        route
        for route in admin_api.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/admin/orders/{order_id}"
        and "PATCH" in route.methods
    ]

    assert registered == []
    assert "def admin_update_order(" not in source
    assert "OrderStatusUpdate" not in source
    assert "ADMIN_MANAGED_ORDER_TRANSITIONS" not in source
    assert "queue_order_status" not in source
    assert "_DELIVERY_STATUSES" not in source


def test_canonical_gateway_owns_the_only_order_patch_contract():
    matching = [
        route
        for route in order_cancellation.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/admin/orders/{order_id}"
        and "PATCH" in route.methods
    ]

    assert len(matching) == 1
    assert matching[0].endpoint is order_cancellation.start_admin_fulfillment_via_generic_patch


def test_monolith_order_source_has_no_direct_cancellation_or_shipping_side_effects():
    source = (ROOT / "api" / "admin.py").read_text(encoding="utf-8")

    assert "datetime.utcnow" not in source
    assert "release_variant" not in source
    assert "LoyaltyRedemptionHold" not in source
    assert 'payload.status == "cancelled"' not in source
    assert "tracking_number =" not in source
    assert "delivery_status =" not in source
