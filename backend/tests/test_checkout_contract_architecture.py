from pathlib import Path

import pytest

from backend.api.orders import _checkout_request_fingerprint
from backend.services.checkout_validation import normalize_checkout_input
from backend.services.delivery import calculate_delivery_price


ROOT = Path(__file__).resolve().parents[2]


def test_checkout_route_validates_before_database_locking():
    source = (ROOT / "backend/api/orders.py").read_text(encoding="utf-8")

    validation_position = source.index("checkout_input = normalize_checkout_input(")
    lock_position = source.index("locked_customer = _lock_checkout_customer")

    assert validation_position < lock_position
    assert "_clean_required" not in source
    assert ".strip()[:2000]" not in source


def test_legacy_delivery_service_is_fail_closed_and_has_no_tariff_fallback():
    source = (ROOT / "backend/services/delivery.py").read_text(encoding="utf-8")

    assert "Delivery price is quote-authoritative" in source
    assert "DeliveryZone" not in source
    assert "default_delivery_price" not in source
    assert "default_pickup_price" not in source
    with pytest.raises(RuntimeError, match="quote-authoritative"):
        calculate_delivery_price(None, "courier", "Москва, Тверская улица, 1")


def test_semantically_identical_checkout_data_has_stable_fingerprint():
    first = normalize_checkout_input(
        name="  Petr   Fedin ",
        phone=" +46   70 123 45 67 ",
        delivery_type=" PICKUP ",
        address="ignored address",
        comment=" note ",
    )
    second = normalize_checkout_input(
        name="Petr Fedin",
        phone="+46 70 123 45 67",
        delivery_type="pickup",
        address="",
        comment="note",
    )

    assert _checkout_request_fingerprint(
        **first.__dict__,
        delivery_quote_id="",
    ) == _checkout_request_fingerprint(
        **second.__dict__,
        delivery_quote_id="",
    )


def test_delivery_quote_identity_changes_checkout_fingerprint():
    normalized = normalize_checkout_input(
        name="Petr Fedin",
        phone="+46 70 123 45 67",
        delivery_type="courier",
        address="Москва, Тверская улица, 1",
        comment="",
    )

    first = _checkout_request_fingerprint(
        **normalized.__dict__,
        delivery_quote_id="dq-first",
    )
    second = _checkout_request_fingerprint(
        **normalized.__dict__,
        delivery_quote_id="dq-requote",
    )
    assert first != second
