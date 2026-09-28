import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api" / "catalog_admin_operations.py"
MAIN = ROOT / "main.py"


def test_feedback_queue_requires_product_read_and_supports_status_filtering():
    source = API.read_text(encoding="utf-8")
    assert '@router.get("/feedback")' in source
    assert 'require_permission(db, admin, "products.read")' in source
    assert 'Literal["published", "hidden"]' in source
    assert "ProductFeedback.product_id == product_id" in source


def test_feedback_queue_does_not_surface_customer_identity_fields():
    source = API.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "admin_feedback_queue"
    )
    function_source = ast.get_source_segment(source, function) or ""
    for forbidden in ("telegram_id", "username", "email", "phone", "customer_id"):
        assert forbidden not in function_source


def test_catalog_operator_router_is_mounted():
    source = MAIN.read_text(encoding="utf-8")
    assert "catalog_admin_operations_router" in source
    assert "app.include_router(catalog_admin_operations_router, prefix=\"/api\")" in source
