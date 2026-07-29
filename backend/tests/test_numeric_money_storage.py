from decimal import Decimal

import pytest
from sqlalchemy import Numeric, inspect, text

from backend.database import engine


_MONEY_COLUMNS = {
    "products": {"price", "old_price"},
    "promo_codes": {"min_amount"},
    "orders": {
        "total_amount",
        "delivery_price",
        "discount_amount",
        "loyalty_discount_amount",
    },
    "order_items": {"price"},
    "payments": {"amount"},
    "return_requests": {"refund_amount"},
    "delivery_zones": {"price"},
    "crm_profiles": {"total_spent", "average_order_value"},
    "payment_reconciliations": {"amount_local", "amount_provider"},
    "delivery_shipments": {"price"},
}

_POINT_COLUMNS = {
    "carts": {"loyalty_points_to_redeem"},
    "orders": {"loyalty_points_redeemed"},
    "crm_profiles": {"loyalty_points"},
    "loyalty_transactions": {"points_delta"},
    "referral_codes": {"reward_points"},
    "loyalty_redemption_holds": {"points"},
    "promo_codes": {"discount_value"},
}


def _columns(table: str):
    return {column["name"]: column for column in inspect(engine).get_columns(table)}


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL migration contract")
def test_transactional_money_columns_use_numeric_20_2():
    for table, names in _MONEY_COLUMNS.items():
        columns = _columns(table)
        for name in names:
            column_type = columns[name]["type"]
            assert isinstance(column_type, Numeric), f"{table}.{name} is {column_type!r}"
            assert column_type.precision == 20
            assert column_type.scale == 2


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL migration contract")
def test_points_and_rates_use_numeric_20_4():
    for table, names in _POINT_COLUMNS.items():
        columns = _columns(table)
        for name in names:
            column_type = columns[name]["type"]
            assert isinstance(column_type, Numeric), f"{table}.{name} is {column_type!r}"
            assert column_type.precision == 20
            assert column_type.scale == 4


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL migration contract")
def test_postgresql_numeric_rounding_is_decimal_and_deterministic():
    with engine.connect() as connection:
        money = connection.execute(
            text("SELECT CAST(:value AS NUMERIC(20,2))"),
            {"value": "10.005"},
        ).scalar_one()
        points = connection.execute(
            text("SELECT CAST(:value AS NUMERIC(20,4))"),
            {"value": "1.23456"},
        ).scalar_one()

    assert money == Decimal("10.01")
    assert points == Decimal("1.2346")


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL migration contract")
def test_optional_old_price_remains_nullable():
    assert _columns("products")["old_price"]["nullable"] is True
