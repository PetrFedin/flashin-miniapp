#!/usr/bin/env python3
"""Run the critical customer money flow through HTTP against a real database.

The script is executed by CI after Alembic migrations. All generated data lives
inside one outer transaction and is rolled back at the end. The only mocked
boundary is the external payment provider; application routes, persistence,
inventory, scheduled pricing, loyalty, promotion, payment settlement,
fulfillment, outbox, and notification code are real.
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

from backend.api import payments as payments_api
from backend.catalog_models import ProductMerchandising
from backend.checkout_models import CheckoutAttempt
from backend.database import engine, get_db
from backend.main import app
from backend.models import (
    Cart,
    CrmProfile,
    Customer,
    FulfillmentTask,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Notification,
    Order,
    OrderItem,
    Payment,
    PaymentEvent,
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
    original_create_payment = payments_api.create_yookassa_payment
    original_fetch_payment = payments_api.fetch_yookassa_payment
    provider_payments: dict[str, dict] = {}
    client: TestClient | None = None

    try:
        customer = Customer(
            telegram_id=str(int(token, 16)),
            username=f"smoke_{token}",
            first_name="Smoke",
        )
        product = Product(
            sku=f"SMOKE-{token}",
            title="Transactional smoke product",
            slug=f"transactional-smoke-{token}",
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
            sku=f"SMOKE-V-{token}",
            stock_qty=5,
            reserved_qty=0,
        )
        promo = PromoCode(
            code=f"SMOKE{token}".upper(),
            discount_type="percent",
            discount_value=10,
            min_amount=0,
            max_uses=1,
            used_count=0,
            active=True,
        )
        db.add_all([customer, product, variant, promo])
        db.flush()
        merchandising = ProductMerchandising(
            product_id=product.id,
            availability_status="in_stock",
            promo_price=900.0,
        )
        profile = CrmProfile(
            customer_id=customer.id,
            segment="smoke",
            loyalty_points=500,
        )
        db.add_all([merchandising, profile])
        db.commit()

        def override_db():
            yield db

        def override_customer():
            return customer

        async def fake_create_yookassa_payment(
            order_id: int,
            amount: float,
            currency: str,
            *,
            attempt: int,
        ) -> dict:
            payment_id = f"smoke-{token}-{order_id}-{attempt}"
            provider_payments[payment_id] = {
                "id": payment_id,
                "status": "succeeded",
                "metadata": {"order_id": str(order_id)},
                "amount": {
                    "value": f"{_money(amount):.2f}",
                    "currency": currency,
                },
                "confirmation": {},
            }
            return {
                "provider_payment_id": payment_id,
                "status": "succeeded",
                "confirmation_url": "",
            }

        async def fake_fetch_yookassa_payment(payment_id: str) -> dict:
            try:
                return provider_payments[payment_id]
            except KeyError as exc:
                raise AssertionError(
                    f"Unexpected provider payment lookup: {payment_id}"
                ) from exc

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_customer] = override_customer
        payments_api.create_yookassa_payment = fake_create_yookassa_payment
        payments_api.fetch_yookassa_payment = fake_fetch_yookassa_payment
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
        assert _money(cart["items"][0]["price"]) == Decimal("900.00")
        assert _money(cart["total_amount"]) == Decimal("1800.00")
        assert _money(cart["final_amount"]) == Decimal("1800.00")

        cart = _expect(
            client.post("/api/cart/promo", json={"code": promo.code}),
            200,
            "apply promotion",
        )
        assert cart["promo_code"] == promo.code
        assert _money(cart["discount_amount"]) == Decimal("180.00")
        assert _money(cart["final_amount"]) == Decimal("1620.00")

        cart = _expect(
            client.post("/api/cart/loyalty", json={"points": 100}),
            200,
            "reserve loyalty points",
        )
        assert _money(cart["final_amount"]) == Decimal("1520.00")

        checkout_key = f"smoke-checkout-{token}"
        checkout_payload = {
            "name": "Smoke Customer",
            "phone": "+46701234567",
            "delivery_type": "pickup",
            "address": "",
            "comment": "Transactional CI smoke",
        }
        order = _expect(
            client.post(
                "/api/orders/checkout",
                headers={"Idempotency-Key": checkout_key},
                json=checkout_payload,
            ),
            200,
            "checkout",
        )
        assert _money(order["total_amount"]) == Decimal("1520.00")
        assert len(order["items"]) == 1
        assert _money(order["items"][0]["price"]) == Decimal("900.00")
        assert order["status"] == "created"
        assert order["payment_status"] == "pending"
        order_id = int(order["id"])

        replayed_order = _expect(
            client.post(
                "/api/orders/checkout",
                headers={"Idempotency-Key": checkout_key},
                json=checkout_payload,
            ),
            200,
            "idempotent checkout replay",
        )
        assert replayed_order["id"] == order_id
        assert _money(replayed_order["items"][0]["price"]) == Decimal("900.00")

        # Remove the live promotion after checkout. The historical OrderItem must
        # retain the locked effective price used for the order and provider amount.
        merchandising.promo_price = None
        db.commit()
        db.expire_all()
        historical_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).one()
        assert _money(historical_item.price) == Decimal("900.00")

        payment = _expect(
            client.post("/api/payments", json={"order_id": order_id}),
            200,
            "create and settle payment",
        )
        assert payment["status"] == "succeeded"
        provider_payment_id = payment["provider_payment_id"]

        webhook_payload = {
            "event": "payment.succeeded",
            "object": {
                "id": provider_payment_id,
                "status": "succeeded",
            },
        }
        first_webhook = _expect(
            client.post("/api/payments/webhook/yookassa", json=webhook_payload),
            200,
            "first payment webhook",
        )
        assert first_webhook["ok"] is True
        replayed_webhook = _expect(
            client.post("/api/payments/webhook/yookassa", json=webhook_payload),
            200,
            "idempotent payment webhook replay",
        )
        assert replayed_webhook == {"ok": True, "idempotent": True}

        db.expire_all()
        persisted_order = db.query(Order).filter(Order.id == order_id).one()
        persisted_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).one()
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        persisted_promo = db.query(PromoCode).filter(PromoCode.id == promo.id).one()
        persisted_profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).one()
        persisted_cart = db.query(Cart).filter(Cart.customer_id == customer.id).one()
        hold = (
            db.query(LoyaltyRedemptionHold)
            .filter(LoyaltyRedemptionHold.order_id == order_id)
            .one()
        )
        loyalty_rows = (
            db.query(LoyaltyTransaction)
            .filter(LoyaltyTransaction.order_id == order_id)
            .order_by(LoyaltyTransaction.id.asc())
            .all()
        )
        notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .all()
        )
        event_keys = (
            db.query(NotificationEventKey)
            .filter(NotificationEventKey.event_key == f"order:{order_id}:paid")
            .all()
        )

        assert persisted_order.status == "paid"
        assert persisted_order.payment_status == "paid"
        assert _money(persisted_order.total_amount) == Decimal("1520.00")
        assert _money(persisted_item.price) == Decimal("900.00")
        assert persisted_variant.stock_qty == 3
        assert persisted_variant.reserved_qty == 0
        assert persisted_promo.used_count == 1
        assert persisted_cart.status == "converted"
        assert hold.status == "committed"
        assert _money(hold.points) == Decimal("100.00")
        assert _money(persisted_profile.loyalty_points) == Decimal("415.20")
        assert [(row.reason, _money(row.points_delta)) for row in loyalty_rows] == [
            ("loyalty_redeemed", Decimal("-100.00")),
            ("order_paid", Decimal("15.20")),
        ]
        assert len(notifications) == 1
        assert len(event_keys) == 1
        assert db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order_id).count() == 1
        assert db.query(Payment).filter(Payment.order_id == order_id).count() == 1
        assert (
            db.query(PaymentEvent)
            .filter(
                PaymentEvent.provider_payment_id == provider_payment_id,
                PaymentEvent.event_type == "payment.succeeded",
            )
            .count()
            == 1
        )
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
                    "scheduled_unit_price": f"{_money(persisted_item.price):.2f}",
                    "total": f"{_money(persisted_order.total_amount):.2f}",
                    "stock_after_sale": persisted_variant.stock_qty,
                    "loyalty_after_sale": f"{_money(persisted_profile.loyalty_points):.2f}",
                    "notifications": len(notifications),
                    "payment_events": 1,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if client is not None:
            client.close()
        payments_api.create_yookassa_payment = original_create_payment
        payments_api.fetch_yookassa_payment = original_fetch_payment
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
