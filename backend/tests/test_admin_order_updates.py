from pathlib import Path

from fastapi.routing import APIRoute

from backend.api import admin as admin_api
from backend.api import order_cancellation


def test_legacy_direct_order_update_helper_is_not_an_exposed_api_route():
    matching = [
        route
        for route in admin_api.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/admin/orders/{order_id}"
        and "PATCH" in route.methods
    ]

    assert len(matching) == 1
    assert matching[0].endpoint is order_cancellation.start_admin_fulfillment_via_generic_patch
    assert matching[0].endpoint is not admin_api.admin_update_order


def test_legacy_order_update_source_has_no_cancellation_side_effects():
    source = (Path(__file__).resolve().parents[1] / "api" / "admin.py").read_text(
        encoding="utf-8"
    )

    assert "datetime.utcnow" not in source
    assert "release_variant" not in source
    assert "LoyaltyRedemptionHold" not in source
    assert 'payload.status == "cancelled"' not in source
