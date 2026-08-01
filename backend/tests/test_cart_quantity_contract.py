from pathlib import Path

from backend.api.cart import router


ROOT = Path(__file__).resolve().parents[2]


def test_storefront_quantity_endpoint_exists_with_expected_shape():
    routes = [route for route in router.routes if route.path == "/cart/items/{item_id}"]
    patch = next(route for route in routes if "PATCH" in route.methods)

    assert patch.name == "update_item"
    assert "quantity" in patch.dependant.query_params[0].name


def test_quantity_change_revalidates_inventory_and_adjustments():
    source = (ROOT / "backend/api/cart.py").read_text(encoding="utf-8")
    start = source.index("def update_item(")
    end = source.index('@router.delete("/items/{item_id}"')
    implementation = source[start:end]

    assert "with_for_update()" in implementation
    assert "variant.available_qty < quantity" in implementation
    assert "return _commit_reconciled_cart(db, cart.id)" in implementation


def test_cart_mutation_response_is_not_serialized_after_commit():
    source = (ROOT / "backend/api/cart.py").read_text(encoding="utf-8")
    start = source.index("def _commit_reconciled_cart")
    end = source.index("@router.get")
    implementation = source[start:end]

    assert implementation.index("response = serialize_cart") < implementation.index("db.commit()")
    assert "return serialize_cart(_load_cart" not in implementation
