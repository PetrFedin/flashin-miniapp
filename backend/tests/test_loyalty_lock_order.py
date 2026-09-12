import os
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.models import CrmProfile, LoyaltyRedemptionHold
from backend.services import cart_adjustments, loyalty
from backend.services.cart_adjustments import CartAdjustmentResult


class RecordingQuery:
    def __init__(self, session, entity):
        self.session = session
        self.entity = entity

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def first(self):
        if self.entity is CrmProfile:
            self.session.events.append("crm_profile")
            return self.session.profile
        return None

    def all(self):
        if self.entity is LoyaltyRedemptionHold:
            self.session.events.append("loyalty_redemption_hold")
            return list(self.session.holds)
        return []


class RecordingSession:
    def __init__(self, *, profile=None, holds=None):
        self.profile = profile
        self.holds = list(holds or [])
        self.events: list[str] = []
        self.added = []

    def query(self, entity):
        return RecordingQuery(self, entity)

    def add(self, value):
        self.added.append(value)
        if isinstance(value, LoyaltyRedemptionHold):
            self.holds.append(value)


def settings(point_value=1, max_percent=30):
    return SimpleNamespace(
        loyalty_point_value_rub=point_value,
        loyalty_max_redeem_percent=max_percent,
    )


def cart(*, points=100, cart_id=7, customer_id=3):
    return SimpleNamespace(
        id=cart_id,
        customer_id=customer_id,
        loyalty_points_to_redeem=points,
        items=[SimpleNamespace(product=SimpleNamespace(price=1000), quantity=1)],
    )


def hold(*, points=100, cart_id=7, customer_id=3, status="reserved"):
    return SimpleNamespace(
        id=17,
        customer_id=customer_id,
        cart_id=cart_id,
        order_id=None,
        points=points,
        status=status,
        released_at=None,
    )


def test_cart_reconciliation_locks_profile_before_reserved_holds(monkeypatch):
    monkeypatch.setattr(cart_adjustments, "get_settings", lambda: settings())
    current_hold = hold()
    db = RecordingSession(
        profile=SimpleNamespace(loyalty_points=500),
        holds=[current_hold],
    )
    current_cart = cart(points=100)

    loyalty_points, loyalty_discount = cart_adjustments._reconcile_loyalty(
        db,
        current_cart,
        Decimal("1000.00"),
        Decimal("0.00"),
    )

    assert db.events == ["crm_profile", "loyalty_redemption_hold"]
    assert loyalty_points == 100
    assert loyalty_discount == Decimal("100.00")
    assert current_hold.status == "reserved"


def test_explicit_request_preserves_profile_then_hold_in_second_pass(monkeypatch):
    events: list[str] = []
    current_hold = hold()
    current_cart = cart(points=100)
    profile = SimpleNamespace(loyalty_points=500)

    monkeypatch.setattr(
        cart_adjustments,
        "reconcile_cart_adjustments",
        lambda _db, _cart: CartAdjustmentResult(
            subtotal=Decimal("1000.00"),
            promo_discount=Decimal("0.00"),
            loyalty_points=100,
            loyalty_discount=Decimal("100.00"),
            promo_code=None,
            unit_prices={},
        ),
    )

    def lock_profile(_db, _customer_id):
        events.append("crm_profile")
        return profile

    def lock_holds(_db, _cart):
        events.append("loyalty_redemption_hold")
        return [current_hold], current_hold

    monkeypatch.setattr(cart_adjustments, "_locked_loyalty_profile", lock_profile)
    monkeypatch.setattr(cart_adjustments, "_reserved_holds", lock_holds)
    monkeypatch.setattr(
        cart_adjustments,
        "_loyalty_settings",
        lambda: (Decimal("1.00"), Decimal("30.00")),
    )

    result = cart_adjustments.apply_loyalty_request(object(), current_cart, 100)

    assert events == ["crm_profile", "loyalty_redemption_hold"]
    assert result.loyalty_points == 100
    assert current_hold.points == Decimal("100.0000")


def test_zero_points_with_missing_profile_still_releases_reserved_hold():
    current_hold = hold(points=50)
    db = RecordingSession(profile=None, holds=[current_hold])
    current_cart = cart(points=0)

    loyalty_points, loyalty_discount = cart_adjustments._reconcile_loyalty(
        db,
        current_cart,
        Decimal("1000.00"),
        Decimal("0.00"),
    )

    assert db.events == ["crm_profile", "loyalty_redemption_hold"]
    assert loyalty_points == 0
    assert loyalty_discount == Decimal("0.00")
    assert current_cart.loyalty_points_to_redeem == Decimal("0.0000")
    assert current_hold.status == "released"
    assert current_hold.released_at is not None


def test_positive_reconciliation_with_missing_profile_fails_safe_to_zero():
    current_hold = hold(points=100)
    db = RecordingSession(profile=None, holds=[current_hold])
    current_cart = cart(points=100)

    loyalty_points, loyalty_discount = cart_adjustments._reconcile_loyalty(
        db,
        current_cart,
        Decimal("1000.00"),
        Decimal("0.00"),
    )

    assert db.events == ["crm_profile", "loyalty_redemption_hold"]
    assert loyalty_points == 0
    assert loyalty_discount == Decimal("0.00")
    assert current_cart.loyalty_points_to_redeem == Decimal("0.0000")
    assert current_hold.status == "released"


def test_compatibility_redeem_points_uses_same_profile_first_order(monkeypatch):
    monkeypatch.setattr(loyalty, "get_settings", lambda: settings())
    current_hold = hold()
    db = RecordingSession(
        profile=SimpleNamespace(loyalty_points=500),
        holds=[current_hold],
    )
    current_cart = cart(points=0)

    loyalty.redeem_points(db, 3, current_cart, 100)

    assert db.events == ["crm_profile", "loyalty_redemption_hold"]
    assert current_cart.loyalty_points_to_redeem == Decimal("100.0000")
    assert current_hold.points == Decimal("100.0000")


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires PostgreSQL row-lock semantics",
)
def test_postgres_loyalty_profile_first_lock_order_smoke():
    from scripts.loyalty_lock_order_smoke import main

    assert main() == 0
