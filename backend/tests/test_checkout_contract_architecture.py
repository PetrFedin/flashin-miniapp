from pathlib import Path

from backend.api.orders import _checkout_request_fingerprint
from backend.services.checkout_validation import normalize_checkout_input


ROOT = Path(__file__).resolve().parents[2]


def test_checkout_route_validates_before_database_locking():
    source = (ROOT / "backend/api/orders.py").read_text(encoding="utf-8")

    validation_position = source.index("checkout_input = normalize_checkout_input(")
    lock_position = source.index("locked_customer = _lock_checkout_customer")

    assert validation_position < lock_position
    assert "_clean_required" not in source
    assert ".strip()[:2000]" not in source


def test_delivery_service_has_no_unknown_type_fallback():
    source = (ROOT / "backend/services/delivery.py").read_text(encoding="utf-8")

    assert "Unsupported delivery type" in source
    assert "return settings.default_delivery_price" not in source


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

    assert _checkout_request_fingerprint(**first.__dict__) == _checkout_request_fingerprint(
        **second.__dict__
    )
