from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.api import cart as cart_api
from backend.database import Base
from backend.models import Cart, CartItem, CrmProfile, Customer, LoyaltyRedemptionHold, Product, ProductVariant, PromoCode
from backend.schemas import LoyaltyRedeemIn, PromoApplyIn
from backend.services.pricing import calculate_discount, calculate_pricing, validate_promo_definition


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings():
    return SimpleNamespace(
        loyalty_point_value_rub=1,
        loyalty_max_redeem_percent=50,
    )


def _cart_fixture(db, *, price=100, quantity=1, stock=10, loyalty_points=0, promo=None):
    customer = Customer(telegram_id=f"pricing-{db.query(Customer).count() + 1}")
    product = Product(
        sku=f"PRICE-PRODUCT-{db.query(Product).count() + 1}",
        title="Pricing product",
        slug=f"pricing-product-{db.query(Product).count() + 1}",
        brand="FLASHIN",
        price=price,
        currency="RUB",
        category="Clothing",
        gender="unisex",
    )
    db.add_all([customer, product])
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="black",
        sku=f"PRICE-VARIANT-{product.id}",
        stock_qty=stock,
        reserved_qty=0,
    )
    db.add(variant)
    db.flush()
    if promo is not None:
        db.add(promo)
        db.flush()
    cart = Cart(
        customer_id=customer.id,
        status="active",
        promo_code_id=promo.id if promo else None,
        loyalty_points_to_redeem=loyalty_points,
    )
    db.add(cart)
    db.flush()
    db.add(
        CartItem(
            cart_id=cart.id,
            product_id=product.id,
            variant_id=variant.id,
            quantity=quantity,
        )
    )
    db.commit()
    return customer, cart_api._load_cart(db, cart.id), product, variant


def _promo(**overrides):
    values = {
        "code": "EXACT",
        "discount_type": "percent",
        "discount_value": 10,
        "min_amount": 0,
        "max_uses": 0,
        "used_count": 0,
        "active": True,
    }
    values.update(overrides)
    return PromoCode(**values)


def test_percent_discount_preserves_four_decimal_rate_and_money_rounding():
    promo = _promo(discount_value=33.3333)
    assert calculate_discount(promo, Decimal("0.30")) == Decimal("0.10")
    assert calculate_discount(promo, Decimal("123.45")) == Decimal("41.15")


def test_fixed_discount_is_capped_at_subtotal_without_negative_total():
    promo = _promo(discount_type="fixed", discount_value=500)
    assert calculate_discount(promo, Decimal("120.00")) == Decimal("120.00")


@pytest.mark.parametrize(
    ("discount_type", "discount_value"),
    [
        ("percent", 0),
        ("percent", 100.0001),
        ("fixed", -1),
        ("unknown", 10),
    ],
)
def test_invalid_promo_definitions_fail_closed(discount_type, discount_value):
    with pytest.raises(HTTPException) as caught:
        validate_promo_definition(discount_type, discount_value)
    assert caught.value.status_code == 400


def test_expired_limited_and_below_minimum_promos_fail_closed():
    with pytest.raises(HTTPException, match="expired"):
        calculate_discount(_promo(expires_at=datetime.utcnow() - timedelta(seconds=1)), 100)
    with pytest.raises(HTTPException, match="usage limit"):
        calculate_discount(_promo(max_uses=1, used_count=1), 100)
    with pytest.raises(HTTPException, match="below promo minimum"):
        calculate_discount(_promo(min_amount=101), 100)


def test_pricing_rejects_loyalty_that_exceeds_amount_left_after_promo():
    promo = _promo(discount_value=80)
    allowed = calculate_pricing(
        subtotal=100,
        promo=promo,
        loyalty_points=15,
        point_value=1,
        max_redeem_percent=50,
    )
    assert allowed.promo_discount == Decimal("80.00")
    assert allowed.loyalty_discount == Decimal("15.00")
    assert allowed.final_amount == Decimal("5.00")

    with pytest.raises(HTTPException, match="exceeds the allowed limit"):
        calculate_pricing(
            subtotal=100,
            promo=promo,
            loyalty_points=25,
            point_value=1,
            max_redeem_percent=50,
        )


def test_delivery_is_added_after_discounts_with_exact_result():
    pricing = calculate_pricing(
        subtotal="199.99",
        promo=_promo(discount_value="12.3456"),
        loyalty_points="10.2500",
        point_value="1.00",
        max_redeem_percent="50",
        delivery_price="300.00",
        require_positive_total=True,
    )
    assert pricing.promo_discount == Decimal("24.69")
    assert pricing.loyalty_discount == Decimal("10.25")
    assert pricing.final_amount == Decimal("465.05")


def test_cart_serialization_rejects_invalid_combination_instead_of_silent_clamp(monkeypatch):
    db = _session()
    monkeypatch.setattr(cart_api, "get_settings", _settings)
    _customer, cart, _product, _variant = _cart_fixture(
        db,
        price=100,
        loyalty_points=25,
        promo=_promo(discount_value=80),
    )

    with pytest.raises(HTTPException, match="exceeds the allowed limit"):
        cart_api.serialize_cart(cart)


def test_applying_promo_rolls_back_when_existing_loyalty_becomes_invalid(monkeypatch):
    db = _session()
    monkeypatch.setattr(cart_api, "get_settings", _settings)
    promo = _promo(discount_value=80)
    customer, cart, _product, _variant = _cart_fixture(db, loyalty_points=25)
    db.add(promo)
    db.commit()

    with pytest.raises(HTTPException, match="exceeds the allowed limit"):
        cart_api.apply_promo(PromoApplyIn(code=promo.code), customer=customer, db=db)

    db.expire_all()
    stored_cart = db.query(Cart).filter(Cart.id == cart.id).one()
    assert stored_cart.promo_code_id is None
    assert stored_cart.loyalty_points_to_redeem == 25


def test_applying_loyalty_rolls_back_before_creating_hold_when_promo_conflicts(monkeypatch):
    db = _session()
    monkeypatch.setattr(cart_api, "get_settings", _settings)
    customer, cart, _product, _variant = _cart_fixture(
        db,
        promo=_promo(discount_value=80),
    )
    db.add(CrmProfile(customer_id=customer.id, segment="new", loyalty_points=100))
    db.commit()

    with pytest.raises(HTTPException, match="exceeds the allowed limit"):
        cart_api.apply_loyalty(LoyaltyRedeemIn(points=25), customer=customer, db=db)

    db.expire_all()
    stored_cart = db.query(Cart).filter(Cart.id == cart.id).one()
    assert stored_cart.loyalty_points_to_redeem == 0
    assert db.query(LoyaltyRedemptionHold).count() == 0


def test_removing_item_is_rejected_before_mutation_when_discounts_would_be_invalid(monkeypatch):
    db = _session()
    monkeypatch.setattr(cart_api, "get_settings", _settings)
    customer, cart, product, _variant = _cart_fixture(
        db,
        price=100,
        loyalty_points=20,
        promo=_promo(discount_value=50),
    )
    second_variant = ProductVariant(
        product_id=product.id,
        size="L",
        color="black",
        sku="PRICE-VARIANT-SECOND",
        stock_qty=10,
        reserved_qty=0,
    )
    db.add(second_variant)
    db.flush()
    second_item = CartItem(
        cart_id=cart.id,
        product_id=product.id,
        variant_id=second_variant.id,
        quantity=1,
    )
    db.add(second_item)
    db.commit()

    # Two items: subtotal 200, promo 100 and 20 loyalty are valid. Removing one
    # leaves only 50 after promo, so 20 remains valid; use 60 points to force conflict.
    stored_cart = db.query(Cart).filter(Cart.id == cart.id).one()
    stored_cart.loyalty_points_to_redeem = 60
    db.commit()

    with pytest.raises(HTTPException, match="Remove or reduce"):
        cart_api.remove_item(second_item.id, customer=customer, db=db)

    db.expire_all()
    assert db.query(CartItem).filter(CartItem.id == second_item.id).one_or_none() is not None
