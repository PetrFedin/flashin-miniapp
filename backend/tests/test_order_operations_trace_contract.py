import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "order_operations_trace.py"
OPS = ROOT / "api" / "ops.py"


def _attribute_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def test_order_trace_never_reads_secret_or_payload_bearing_fields():
    attributes = _attribute_names(SERVICE)

    forbidden = {
        "raw_payload",
        "payload_json",
        "idempotency_key",
        "request_fingerprint",
        "telegram_id",
        "message",
        "last_error",
        "error",
        "confirmation_url",
        "source",
    }
    assert attributes.isdisjoint(forbidden)


def test_order_trace_keeps_durable_order_correlation_and_all_pilot_spine_sections():
    source = SERVICE.read_text(encoding="utf-8")

    assert '"schema_version": 2' in source
    assert '"correlation": {"type": "order_id"' in source
    for section in (
        '"checkout"',
        '"payments"',
        '"payment_events"',
        '"returns"',
        '"provider_commands"',
        '"inventory"',
        '"fulfillment"',
        '"business_events"',
        '"notifications"',
        '"sla"',
        '"attention"',
    ):
        assert section in source


def test_inventory_trace_is_order_scoped_sanitized_and_fail_closed():
    source = SERVICE.read_text(encoding="utf-8")

    assert "InventoryMovement.order_id == order_id" in source
    assert "def _inventory_movement(" in source
    assert '"variant_id": int(movement.variant_id)' in source
    assert '"kind": str(movement.kind)' in source
    assert '"stock_before": int(movement.stock_before)' in source
    assert '"stock_after": int(movement.stock_after)' in source
    assert '"reserved_before": int(movement.reserved_before)' in source
    assert '"reserved_after": int(movement.reserved_after)' in source
    assert '"inventory_invalid_rows": inventory_invalid_rows' in source
    assert "or inventory_invalid_rows" in source
    assert "movement.source" not in source


def test_ops_trace_is_read_only_no_store_and_orders_read_protected():
    source = OPS.read_text(encoding="utf-8")

    route = '@router.get("/orders/{order_id}/trace")'
    assert route in source
    trace_source = source[source.index(route): source.index('@router.get("/abandoned-carts"')]
    assert 'require_permission(db, admin, "orders.read")' in trace_source
    assert 'response.headers["Cache-Control"] = "no-store, max-age=0"' in trace_source
    assert 'response.headers["Pragma"] = "no-cache"' in trace_source
    assert 'trace["request_id"]' in trace_source
