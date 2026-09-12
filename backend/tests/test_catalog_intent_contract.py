import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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


def test_intent_timestamps_are_serialized_as_explicit_utc():
    naive = datetime(2026, 8, 17, 8, 30, 0)
    aware = datetime(2026, 8, 17, 11, 30, 0, tzinfo=timezone(timedelta(hours=3)))

    assert catalog_intents._iso_utc(naive) == "2026-08-17T08:30:00Z"
    assert catalog_intents._iso_utc(aware) == "2026-08-17T08:30:00Z"
    assert catalog_intents._iso_utc(None) is None


def test_explicit_null_admin_fields_are_distinguishable_from_omitted_fields():
    omitted = catalog_intents.ProductIntentAdminUpdate()
    clearing = catalog_intents.ProductIntentAdminUpdate(
        quote_amount=None,
        estimated_ready_at=None,
        admin_note=None,
    )

    assert "quote_amount" not in omitted.model_fields_set
    assert "estimated_ready_at" not in omitted.model_fields_set
    assert {"quote_amount", "estimated_ready_at", "admin_note"}.issubset(clearing.model_fields_set)
    source = inspect.getsource(catalog_intents.update_admin_product_intent)
    assert "payload.model_fields_set" in source


def test_only_valid_zero_available_variants_are_intent_eligible():
    zero_stock = SimpleNamespace(stock_qty=0, reserved_qty=0, available_qty=0)
    fully_reserved = SimpleNamespace(stock_qty=2, reserved_qty=2, available_qty=0)
    in_stock = SimpleNamespace(stock_qty=2, reserved_qty=0, available_qty=2)
    invalid = SimpleNamespace(stock_qty=1, reserved_qty=2, available_qty=-1)

    assert catalog_intents._intent_variant_eligible(zero_stock) is True
    assert catalog_intents._intent_variant_eligible(fully_reserved) is True
    assert catalog_intents._intent_variant_eligible(in_stock) is False
    assert catalog_intents._intent_variant_eligible(invalid) is False
