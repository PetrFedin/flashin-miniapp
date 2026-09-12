from pathlib import Path
from types import SimpleNamespace

from backend.api.admin_returns import _customer_fields


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "admin_returns.py"
    ).read_text(encoding="utf-8")


def test_customer_fields_fail_closed_when_pii_is_not_visible():
    customer = SimpleNamespace(
        id=42,
        first_name="Private",
        last_name="Customer",
        username="private_customer",
        phone="+46700000000",
    )

    assert _customer_fields(customer, False) == {
        "customer_pii_visible": False,
        "customer_id": None,
        "customer_name": "",
        "customer_username": "",
        "customer_phone": "",
    }


def test_customer_fields_preserve_identity_only_when_authorized():
    customer = SimpleNamespace(
        id=42,
        first_name="Private",
        last_name="Customer",
        username="private_customer",
        phone="+46700000000",
    )

    assert _customer_fields(customer, True) == {
        "customer_pii_visible": True,
        "customer_id": 42,
        "customer_name": "Private Customer",
        "customer_username": "private_customer",
        "customer_phone": "+46700000000",
    }


def test_returns_route_requires_orders_read_and_separately_checks_customer_read():
    source = _source()

    assert 'require_permission(db, admin, "orders.read")' in source
    assert 'can_read_customer = has_permission(db, admin, "customers.read")' in source
    assert 'if can_read_customer and rows:' in source
    assert 'db.query(Customer).filter(Customer.id.in_(customer_ids)).all()' in source
    assert '.join(Customer,' not in source
    assert 'customer = customers_by_id.get(return_request.customer_id) if can_read_customer else None' in source
