from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import Float, text

from backend.database import Base, SessionLocal
import backend.model_constraints  # noqa: F401 - applies runtime metadata contracts
from backend.models import Product, PromoCode
from backend.money_model_types import FixedDecimal, MONEY_COLUMNS, POINTS_COLUMNS
from backend.services import event_dispatcher, outbox
from backend.services.payments import _validate_positive_amount
from backend.services.promos import calculate_discount


def test_transactional_metadata_uses_decimal_and_nonfinancial_scores_stay_float():
    for table_name, column_name in MONEY_COLUMNS:
        column_type = Base.metadata.tables[table_name].c[column_name].type
        assert isinstance(column_type, FixedDecimal), f"{table_name}.{column_name} is not fixed precision"
        assert column_type.precision == 20
        assert column_type.scale == 2
        assert column_type.python_type is Decimal

    for table_name, column_name in POINTS_COLUMNS:
        column_type = Base.metadata.tables[table_name].c[column_name].type
        assert isinstance(column_type, FixedDecimal), f"{table_name}.{column_name} is not fixed precision"
        assert column_type.precision == 20
        assert column_type.scale == 4
        assert column_type.python_type is Decimal

    assert isinstance(Base.metadata.tables["product_recommendations"].c.score.type, Float)
    assert isinstance(Base.metadata.tables["moysklad_sku_matches"].c.confidence.type, Float)


def test_fixed_decimal_normalizes_binary_float_and_half_up_rounding():
    money = FixedDecimal(precision=20, scale=2)
    points = FixedDecimal(precision=20, scale=4)

    assert money.process_bind_param(0.1 + 0.2, None) == Decimal("0.30")
    assert money.process_bind_param("1.005", None) == Decimal("1.01")
    assert points.process_bind_param("1.23456", None) == Decimal("1.2346")

    with pytest.raises(ValueError):
        money.process_bind_param(Decimal("NaN"), None)
    with pytest.raises(ValueError):
        money.process_bind_param(Decimal("Infinity"), None)


def test_provider_money_boundary_rounds_half_up_before_validation():
    assert _validate_positive_amount(Decimal("1.005"), "Payment") == Decimal("1.01")
    assert _validate_positive_amount(0.1 + 0.2, "Payment") == Decimal("0.30")

    with pytest.raises(Exception) as too_small:
        _validate_positive_amount(Decimal("0.004"), "Payment")
    assert getattr(too_small.value, "status_code", None) == 400

    assert _validate_positive_amount(Decimal("0.005"), "Payment") == Decimal("0.01")


def test_promo_discount_is_exact_decimal_at_currency_boundary():
    promo = PromoCode(
        code="FIXEDPRECISION",
        discount_type="percent",
        discount_value=Decimal("12.5000"),
        min_amount=Decimal("0.00"),
        max_uses=0,
        used_count=0,
        active=True,
    )

    discount = calculate_discount(promo, Decimal("19.99"))

    assert isinstance(discount, Decimal)
    assert discount == Decimal("2.50")


def test_decimal_event_and_webhook_payloads_keep_existing_json_number_contract():
    event_payload = json.loads(event_dispatcher._serialize_payload({"amount": Decimal("19.90")}))
    webhook_payload = json.loads(outbox._serialize_payload({"amount": Decimal("19.90")}))

    assert event_payload == {"amount": 19.9}
    assert webhook_payload == {"amount": 19.9}

    with pytest.raises(event_dispatcher.BusinessEventPayloadError):
        event_dispatcher._serialize_payload({"amount": Decimal("NaN")})
    with pytest.raises(ValueError):
        outbox._serialize_payload({"amount": Decimal("NaN")})


def test_postgresql_migration_has_numeric_precision_for_every_transactional_column():
    db = SessionLocal()
    try:
        if db.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL schema assertion")

        for table_name, column_name in MONEY_COLUMNS + POINTS_COLUMNS:
            expected_scale = 2 if (table_name, column_name) in MONEY_COLUMNS else 4
            row = db.execute(
                text(
                    """
                    SELECT data_type, numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = :table_name
                      AND column_name = :column_name
                    """
                ),
                {"table_name": table_name, "column_name": column_name},
            ).mappings().one()
            assert row["data_type"] == "numeric", f"{table_name}.{column_name} is not NUMERIC"
            assert row["numeric_precision"] == 20
            assert row["numeric_scale"] == expected_scale
    finally:
        db.close()


def test_postgresql_orm_round_trip_returns_quantized_decimal_values():
    db = SessionLocal()
    marker = uuid.uuid4().hex
    try:
        product = Product(
            sku=f"money-{marker}",
            title="Fixed precision test",
            slug=f"fixed-precision-{marker}",
            price=0.1 + 0.2,
            old_price="1.005",
        )
        promo = PromoCode(
            code=f"FP{marker[:12].upper()}",
            discount_type="percent",
            discount_value="12.34567",
            min_amount=0.1 + 0.2,
            max_uses=0,
            used_count=0,
            active=True,
        )
        db.add_all([product, promo])
        db.flush()
        db.refresh(product)
        db.refresh(promo)

        assert product.price == Decimal("0.30")
        assert product.old_price == Decimal("1.01")
        assert promo.min_amount == Decimal("0.30")
        assert promo.discount_value == Decimal("12.3457")
        assert isinstance(product.price, Decimal)
        assert isinstance(promo.discount_value, Decimal)
    finally:
        db.rollback()
        db.close()
