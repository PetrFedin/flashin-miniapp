from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.models import CrmProfile, LoyaltyRedemptionHold, PromoCode
from backend.services import cart_adjustments
from backend.services.cart_adjustments import apply_loyalty_request, reconcile_cart_adjustments


class FakeQuery:
    def __init__(self, session, entity):
        self.session = session
        self.entity = entity

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        if self.entity is PromoCode:
            return self.session.promo
        if self.entity is CrmProfile:
            return self.session.profile
        return None

    def all(self):
        if self.entity is LoyaltyRedemptionHold:
            return [hold for hold in self.session.holds if hold.status == "reserved"]
        return []


class FakeSession:
    def __init__(self, *, promo=None, profile=None, holds=None):
        self.promo = promo
        self.profile = profile
        self.holds = list(holds or [])
        self.added = []

    def query(self, entity):
        return FakeQuery(self, entity)

    def add(self, value):
        self.added.append(value)
        if isinstance(value, LoyaltyRedemptionHold):
            self.holds.append(value)


def settings(point_value=1, max_percent=30):
    return SimpleNamespace(
        loyalty_point_value_rub=point_value,
        loyalty_max_redeem_percent=max_percent,
    )


def item(price, quantity=1, item_id=1):
    return SimpleNamespace(
        id=item_id,
        quantity=quantity,
        product=SimpleNamespace(price=price),
    )


def cart(*, items, promo_id=None, points=0, cart_id=7, customer_id=3):
    return SimpleNamespace(
        id=cart_id,
        customer_id=customer_id,
        items=list(items),
        promo_code_id=promo_id,
        promo_code=None,
        loyalty_points_to_redeem=points,
    )


def promo(**overrides):
    values = {
        "id": 11,
        "code": "SAVE50",
        "active": True,
        "expires_at": None,
        "max_uses": 0,
        "used_count": 0,
        "min_amount": 0,
        "discount_type": "percent",
        "discount_value": 50,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def hold(points, *, cart_id=7, status="reserved"):
    return SimpleNamespace(
        id=1,
        customer_id=3,
        cart_id=cart_id,
        order_id=None,
        points=points,
        status=status,
        released_at=None,
    )


def test_reconciliation_detaches_promo_that_no_longer_meets_minimum(monkeypatch):
    monkeypatch.setattr(cart_adjustments, "get_settings", lambda: settings())
    current = cart(items=[item(200)], promo_id=11)
    db = FakeSession(promo=promo(min_amount=500))

    result = reconcile_cart_adjustments(db, current)

    assert current.promo_code_id is None
    assert result.promo_code is None
    assert result.promo_discount == 0
    assert result.final_amount == 200


def test_reconciliation_caps_stale_hold_after_cart_or_promo_change(monkeypatch):
    monkeypatch.setattr(cart_adjustments, "get_settings", lambda: settings())
    current_hold = hold(500)
    current = cart(items=[item(1000)], promo_id=11, points=500)
    db = FakeSession(
        promo=promo(discount_value=50),
        profile=SimpleNamespace(loyalty_points=1000),
        holds=[current_hold],
    )

    result = reconcile_cart_adjustments(db, current)

    assert result.promo_discount == 500
    assert result.loyalty_points == 300
    assert result.loyalty_discount == 300
    assert result.final_amount == 200
    assert current.loyalty_points_to_redeem == 300
    assert current_hold.points == 300
    assert current_hold.status == "reserved"


def test_empty_cart_releases_existing_loyalty_hold(monkeypatch):
    monkeypatch.setattr(cart_adjustments, "get_settings", lambda: settings())
    current_hold = hold(100)
    current = cart(items=[], points=100)
    db = FakeSession(
        profile=SimpleNamespace(loyalty_points=1000),
        holds=[current_hold],
    )

    result = reconcile_cart_adjustments(db, current)

    assert result.loyalty_points == 0
    assert result.final_amount == 0
    assert current.loyalty_points_to_redeem == 0
    assert current_hold.status == "released"
    assert current_hold.released_at is not None


def test_missing_hold_is_recreated_for_valid_saved_points(monkeypatch):
    monkeypatch.setattr(cart_adjustments, "get_settings", lambda: settings())
    current = cart(items=[item(1000)], points=100)
    db = FakeSession(profile=SimpleNamespace(loyalty_points=500))

    result = reconcile_cart_adjustments(db, current)

    assert result.loyalty_points == 100
    assert len(db.added) == 1
    assert db.added[0].points == 100
    assert db.added[0].status == "reserved"


def test_explicit_loyalty_request_rejects_fractional_points(monkeypatch):
    monkeypatch.setattr(cart_adjustments, "get_settings", lambda: settings())
    current = cart(items=[item(1000)])
    db = FakeSession(profile=SimpleNamespace(loyalty_points=500))

    with pytest.raises(HTTPException) as error:
        apply_loyalty_request(db, current, 10.5)

    assert error.value.status_code == 422
    assert error.value.detail == "Loyalty points must be whole numbers"


def test_explicit_loyalty_request_rejects_amount_above_promo_adjusted_limit(monkeypatch):
    monkeypatch.setattr(cart_adjustments, "get_settings", lambda: settings(max_percent=100))
    current = cart(items=[item(1000)], promo_id=11)
    db = FakeSession(
        promo=promo(discount_value=80),
        profile=SimpleNamespace(loyalty_points=1000),
    )

    with pytest.raises(HTTPException) as error:
        apply_loyalty_request(db, current, 201)

    assert error.value.status_code == 409
    assert error.value.detail == "No more than 200 loyalty points can be redeemed for this cart"


def test_explicit_loyalty_request_is_applied_exactly(monkeypatch):
    monkeypatch.setattr(cart_adjustments, "get_settings", lambda: settings())
    current = cart(items=[item(1000)])
    db = FakeSession(profile=SimpleNamespace(loyalty_points=500))

    result = apply_loyalty_request(db, current, 250)

    assert result.loyalty_points == 250
    assert result.loyalty_discount == 250
    assert current.loyalty_points_to_redeem == 250
    assert db.holds[-1].points == 250
