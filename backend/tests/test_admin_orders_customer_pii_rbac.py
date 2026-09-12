from pathlib import Path
from types import SimpleNamespace

from backend.api.admin import _admin_order_out


def _order():
    return SimpleNamespace(
        id=77,
        status="paid",
        payment_status="paid",
        delivery_status="pending",
        total_amount=12000.0,
        delivery_price=0.0,
        discount_amount=0.0,
        currency="RUB",
        delivery_type="courier",
        address="Private street 1",
        comment="Call customer before delivery",
        tracking_number="track-77",
        items=[],
    )


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "admin.py"
    ).read_text(encoding="utf-8")


def test_order_contact_fields_are_redacted_without_customer_read():
    payload = _admin_order_out(_order(), False)

    assert payload.address == ""
    assert payload.comment == ""
    assert payload.id == 77
    assert payload.status == "paid"
    assert payload.total_amount == 12000.0
    assert payload.tracking_number == "track-77"


def test_order_contact_fields_remain_available_with_customer_read():
    payload = _admin_order_out(_order(), True)

    assert payload.address == "Private street 1"
    assert payload.comment == "Call customer before delivery"


def test_admin_orders_keep_operational_read_separate_from_customer_pii():
    source = _source()

    assert 'require_permission(db, admin, "orders.read")' in source
    assert source.count('can_read_customer = has_permission(db, admin, "customers.read")') >= 2
    assert 'return [_admin_order_out(order, can_read_customer) for order in orders]' in source


def test_order_csv_redacts_customer_link_and_address_without_customer_read():
    source = _source()

    assert 'order.customer_id if can_read_customer else ""' in source
    assert 'order.address if can_read_customer else ""' in source
