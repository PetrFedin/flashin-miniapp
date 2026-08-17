import inspect

from backend.api import catalog_intents
from backend.catalog_intent_models import ProductIntentRequest


def test_intent_table_is_not_an_order_or_payment_record():
    columns = set(ProductIntentRequest.__table__.columns.keys())
    assert {"customer_id", "product_id", "intent_type", "status", "active_request_key"}.issubset(columns)
    assert "order_id" not in columns
    assert "payment_id" not in columns
    assert "reserved_qty" not in columns


def test_create_intent_does_not_mutate_checkout_or_inventory_models():
    source = inspect.getsource(catalog_intents.create_product_intent)
    assert "Cart(" not in source
    assert "Order(" not in source
    assert "Payment(" not in source
    assert "adjust_stock" not in source
    assert ".stock_qty =" not in source
    assert ".reserved_qty =" not in source


def test_active_request_key_prevents_duplicate_active_variant_intents():
    assert catalog_intents._active_key(7, 9, 11) == "7:9:11"
    assert catalog_intents._active_key(7, 9, None) == "7:9:0"


def test_status_machine_is_fail_closed_after_terminal_state():
    assert catalog_intents._ALLOWED_TRANSITIONS["requested"] == {"requested", "working", "cancelled"}
    assert catalog_intents._ALLOWED_TRANSITIONS["working"] == {"working", "ready", "cancelled"}
    assert catalog_intents._ALLOWED_TRANSITIONS["ready"] == {"ready", "closed", "cancelled"}
    assert catalog_intents._ALLOWED_TRANSITIONS["closed"] == {"closed"}
    assert catalog_intents._ALLOWED_TRANSITIONS["cancelled"] == {"cancelled"}
