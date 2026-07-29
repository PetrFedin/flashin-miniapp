from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import Cart, CartItem, CrmProfile, Customer, Product, ProductVariant
from backend.services import loyalty


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _customer_with_profile(db, points=0):
    customer = Customer(telegram_id=f"loyalty-{db.query(Customer).count()}")
    db.add(customer)
    db.flush()
    profile = CrmProfile(customer_id=customer.id, segment="new", loyalty_points=points)
    db.add(profile)
    db.commit()
    return customer, profile


def _cart_with_item(db, customer, price=0.10, quantity=3):
    product = Product(
        sku=f"PRODUCT-{db.query(Product).count()}",
        title="Decimal product",
        slug=f"decimal-product-{db.query(Product).count()}",
        brand="FLASHIN",
        price=price,
        currency="RUB",
        category="Clothing",
        gender="unisex",
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="black",
        sku=f"VARIANT-{product.id}",
        stock_qty=10,
        reserved_qty=0,
    )
    db.add(variant)
    db.flush()
    cart = Cart(customer_id=customer.id, status="active")
    db.add(cart)
    db.flush()
    db.add(CartItem(cart_id=cart.id, variant_id=variant.id, quantity=quantity))
    db.commit()
    return db.query(Cart).filter(Cart.id == cart.id).one()


def test_fractional_point_additions_are_quantized_before_accumulation():
    db = _session()
    customer, _profile = _customer_with_profile(db)

    for _ in range(10):
        loyalty.add_points(db, customer.id, 0.00006, "fractional")
    db.commit()

    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).one()
    assert profile.loyalty_points == pytest.approx(0.001)
    deltas = [row.points_delta for row in profile.transactions] if hasattr(profile, "transactions") else []
    assert db.query(loyalty.LoyaltyTransaction).count() == 10
    assert deltas == [] or sum(deltas) == pytest.approx(0.001)


def test_loyalty_balance_cannot_become_negative_after_exact_rounding():
    db = _session()
    customer, profile = _customer_with_profile(db, points=1.0000)

    loyalty.add_points(db, customer.id, -1.0, "redeem")
    db.flush()
    assert profile.loyalty_points == 0

    with pytest.raises(HTTPException) as caught:
        loyalty.add_points(db, customer.id, -0.0001, "overdraft")
    assert caught.value.status_code == 409
    assert profile.loyalty_points == 0


def test_redeem_limit_uses_decimal_money_not_binary_float(monkeypatch):
    db = _session()
    customer, _profile = _customer_with_profile(db, points=100)
    cart = _cart_with_item(db, customer, price=0.10, quantity=3)
    monkeypatch.setattr(
        loyalty,
        "get_settings",
        lambda: SimpleNamespace(
            loyalty_max_redeem_percent=10,
            loyalty_point_value_rub=0.01,
        ),
    )

    loyalty.redeem_points(db, customer.id, cart, 3)
    db.commit()
    assert cart.loyalty_points_to_redeem == 3

    with pytest.raises(HTTPException) as caught:
        loyalty.redeem_points(db, customer.id, cart, 3.5)
    assert caught.value.status_code == 409
    assert "Max loyalty redemption" in str(caught.value.detail)


def test_other_cart_holds_reduce_available_points_exactly(monkeypatch):
    db = _session()
    customer, _profile = _customer_with_profile(db, points=5)
    first_cart = _cart_with_item(db, customer, price=10, quantity=1)
    second_cart = Cart(customer_id=customer.id, status="converted")
    db.add(second_cart)
    db.flush()
    loyalty.create_redemption_hold(db, customer.id, second_cart.id, 2.50005)
    db.commit()
    monkeypatch.setattr(
        loyalty,
        "get_settings",
        lambda: SimpleNamespace(
            loyalty_max_redeem_percent=100,
            loyalty_point_value_rub=0.01,
        ),
    )

    with pytest.raises(HTTPException) as caught:
        loyalty.redeem_points(db, customer.id, first_cart, 2.5)
    assert caught.value.status_code == 409
    assert "Not enough available" in str(caught.value.detail)

    loyalty.redeem_points(db, customer.id, first_cart, 2.4999)
    db.commit()
    assert first_cart.loyalty_points_to_redeem == pytest.approx(2.4999)


def test_invalid_nonfinite_points_are_rejected():
    db = _session()
    customer, _profile = _customer_with_profile(db)

    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(HTTPException) as caught:
            loyalty.add_points(db, customer.id, value, "invalid")
        assert caught.value.status_code == 400


def test_referral_code_creation_does_not_commit_caller_transaction(monkeypatch):
    db = _session()
    customer, _profile = _customer_with_profile(db)
    original_commit = db.commit
    commit_calls = []

    def unexpected_commit():
        commit_calls.append(True)
        raise AssertionError("service must not commit caller transaction")

    monkeypatch.setattr(db, "commit", unexpected_commit)
    referral = loyalty.ensure_referral_code(db, customer.id)

    assert referral.id is not None
    assert referral.code.startswith("FL")
    assert commit_calls == []

    monkeypatch.setattr(db, "commit", original_commit)
    db.commit()
    repeated = loyalty.ensure_referral_code(db, customer.id)
    assert repeated.id == referral.id


def test_referral_advisory_lock_is_stable_and_customer_specific():
    assert loyalty._referral_lock_key(42) == loyalty._referral_lock_key(42)
    assert loyalty._referral_lock_key(42) != loyalty._referral_lock_key(43)
