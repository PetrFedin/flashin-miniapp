from pathlib import Path

from backend.api.cart import router


ROOT = Path(__file__).resolve().parents[2]


def test_quantity_patch_route_is_registered():
    matching = [route for route in router.routes if route.path == "/cart/items/{item_id}"]

    assert any("PATCH" in route.methods for route in matching)
    assert any("DELETE" in route.methods for route in matching)


def test_cart_response_is_built_before_commit():
    source = (ROOT / "backend/api/cart.py").read_text(encoding="utf-8")
    helper = source[source.index("def _commit_reconciled_cart"):source.index("@router.get")]

    assert helper.index("response = serialize_cart") < helper.index("db.commit()")
    assert helper.index("_flush(db)") < helper.index("reconcile_cart_adjustments")
    assert "populate_existing" in source


def test_all_cart_mutations_use_adjustment_reconciliation():
    source = (ROOT / "backend/api/cart.py").read_text(encoding="utf-8")

    assert source.count("return _commit_reconciled_cart(db, cart.id)") >= 6
    assert "apply_loyalty_request" in source
    assert "redeem_points" not in source


def test_adjustment_service_owns_hold_release_timestamp():
    source = (ROOT / "backend/services/cart_adjustments.py").read_text(encoding="utf-8")

    assert "hold.released_at = utcnow_naive()" in source
    assert "No more than {allowed_points} loyalty points" in source
