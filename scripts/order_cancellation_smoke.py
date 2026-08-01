#!/usr/bin/env python3
"""Prove unpaid order cancellation releases every reservation exactly once.

The script executes the real cart, checkout, and cancellation HTTP routes against
PostgreSQL after migrations. Generated data is isolated by an outer transaction
and rolled back. No application boundary is mocked because payment creation must
not occur in this scenario.
"""

from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.checkout_models import CheckoutAttempt
from backend.database import engine, get_db
from backend.main import app
from backend.models import (
    Cart,
    CrmProfile,
    Customer,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Notification,
    Order,
    Payment,
    Product,
    ProductVariant,
    PromoCode,
)
from backend.notification_models import NotificationEventKey
from backend.security import get_current_customer


def _money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _expect(response, status_code: int, step: str) -> dict:
    if response.status_code != status_code:
        raise AssertionError(
            f"{step} returned HTTP {response.status_code}: {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise AssertionError(f"{step} returned a non-object payload: {payload!r}")
    return payload


def main() -> int:
    token = uuid.uuid4().hex[:20]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    previous_overrides = dict(app.dependency_overrides)
    client: TestClient | None = None

    try:
        customer = Customer(
            telegram_id=str(int(token, 16)),
            username=f"cancel_{token}",
            first_name="Cancellation",
        )
        product = Product(
            sku=f"CANCEL-{token}",
            title="Cancellation smoke product",
            slug=f"cancellation-smoke-{token}",
            brand="FLASHIN",
            price=1000.0,
            currency="RUB",
            category="Testing",
            gender="unisex",
            active=True,
        )
        variant = ProductVariant(
            product=product,
            size="M",
            color="Black",
            sku=f"CANCEL-V-{token}",
            stock_qty=5,
            reserved_qty=0,
        )
        promo = PromoCode(
            code=f"CANCEL{token}".upper(),
            discount_type="percent",
            discount_value=10,
            min_amount=0,
            max_uses=1,
            used_count=0,
            active=True,
        )
        db.add_all([customer, product, variant, promo])
        db.flush()
        profile = CrmProfile(
            customer_id=customer.id,
            segment="cancellation-smoke",
            loyalty_points=500,
        )
        db.add(profile)
        db.commit()

        def override_db():
            yield db

        def override_customer():
            return customer

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_customer] = override_customer
        client = TestClient(app)

        cart = _expect(
            client.post(
                "/api/cart/items",
                json={
                    "product_id": product.id,
                    "variant_id": variant.id,
                    "quantity": 2,
                },
            ),
            200,
            "add cart item",
        )
        assert _money(cart["total_amount"]) == Decimal("2000.00")

        cart = _expect(
            client.post("/api/cart/promo", json={"code": promo.code}),
            200,
            "apply promotion",
        )
        assert _money(cart["promo_discount_amount"]) == Decimal("200.00")

        cart = _expect(
            client.post("/api/cart/loyalty", json={"points": 100}),
            200,
            "reserve loyalty points",
        )
        assert cart["loyalty_points_reserved"] == 100
        assert _money(cart["loyalty_discount_amount"]) == Decimal("100.00")
        assert _money(cart["discount_amount"]) == Decimal("300.00")
        assert _money(cart["final_amount"]) == Decimal("1700.00")

        checkout_key = f"cancel-checkout-{token}"
        order = _expect(
            client.post(
                "/api/orders/checkout",
                headers={"Idempotency-Key": checkout_key},
                json={
                    "name": "Cancellation Customer",
                    "phone": "+46701234567",
                    "delivery_type": "pickup",
                    "address": "",
                    "comment": "Transactional cancellation CI smoke",
                },
            ),
            200,
            "checkout",
        )
        order_id = int(order["id"])
        assert order["status"] == "created"
        assert order["payment_status"] == "pending"
        assert order["delivery_status"] == "not_started"
        assert _money(order["total_amount"]) == Decimal("1700.00")

        db.expire_all()
        reserved_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        reserved_promo = db.query(PromoCode).filter(PromoCode.id == promo.id).one()
        reserved_hold = (
            db.query(LoyaltyRedemptionHold)
            .filter(LoyaltyRedemptionHold.order_id == order_id)
            .one()
        )
        assert reserved_variant.stock_qty == 5
        assert reserved_variant.reserved_qty == 2
        assert reserved_promo.used_count == 1
        assert reserved_hold.status == "reserved"
        assert _money(reserved_hold.points) == Decimal("100.00")
        assert db.query(Payment).filter(Payment.order_id == order_id).count() == 0

        cancelled = _expect(
            client.post(f"/api/orders/{order_id}/cancel"),
            200,
            "cancel unpaid order",
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["payment_status"] == "cancelled"
        assert cancelled["delivery_status"] == "cancelled"

        replayed = _expect(
            client.post(f"/api/orders/{order_id}/cancel"),
            200,
            "idempotent cancellation replay",
        )
        assert replayed["id"] == order_id
        assert replayed["status"] == "cancelled"
        assert replayed["payment_status"] == "cancelled"
        assert replayed["delivery_status"] == "cancelled"

        db.expire_all()
        persisted_order = db.query(Order).filter(Order.id == order_id).one()
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        persisted_promo = db.query(PromoCode).filter(PromoCode.id == promo.id).one()
        persisted_profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).one()
        persisted_cart = db.query(Cart).filter(Cart.customer_id == customer.id).one()
        persisted_hold = (
            db.query(LoyaltyRedemptionHold)
            .filter(LoyaltyRedemptionHold.order_id == order_id)
            .one()
        )
        notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .all()
        )
        event_key = f"order:{order_id}:status:cancelled:delivery:cancelled"
        event_keys = (
            db.query(NotificationEventKey)
            .filter(NotificationEventKey.event_key == event_key)
            .all()
        )

        assert persisted_order.status == "cancelled"
        assert persisted_order.payment_status == "cancelled"
        assert persisted_order.delivery_status == "cancelled"
        assert persisted_variant.stock_qty == 5
        assert persisted_variant.reserved_qty == 0
        assert persisted_promo.used_count == 0
        assert persisted_hold.status == "released"
        assert persisted_hold.released_at is not None
        assert _money(persisted_profile.loyalty_points) == Decimal("500.00")
        assert persisted_cart.status == "converted"
        assert db.query(Payment).filter(Payment.order_id == order_id).count() == 0
        assert db.query(LoyaltyTransaction).filter(LoyaltyTransaction.order_id == order_id).count() == 0
        assert len(notifications) == 1
        assert len(event_keys) == 1
        assert (
            db.query(CheckoutAttempt)
            .filter(
                CheckoutAttempt.customer_id == customer.id,
                CheckoutAttempt.idempotency_key == checkout_key,
            )
            .count()
            == 1
        )

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "stock_after_cancel": persisted_variant.stock_qty,
                    "reserved_after_cancel": persisted_variant.reserved_qty,
                    "promo_uses_after_cancel": persisted_promo.used_count,
                    "loyalty_after_cancel": f"{_money(persisted_profile.loyalty_points):.2f}",
                    "notifications": len(notifications),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if client is not None:
            client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
