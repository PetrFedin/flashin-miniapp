#!/usr/bin/env python3
"""Prove that a late provider success after cancellation becomes a durable review case.

The scenario uses real FastAPI routes and a migrated PostgreSQL schema. All data
is isolated in an outer transaction and rolled back. Only the external payment
provider boundary is replaced with an in-process deterministic fake.
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
from backend.checkout_models import CheckoutAttempt
from backend.database import engine, get_db
from backend.main import app
from backend.models import (
    BusinessEvent,
    Cart,
    CrmProfile,
    Customer,
    FulfillmentTask,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Notification,
    Order,
    Payment,
    PaymentEvent,
    PaymentReconciliation,
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
            username=f"review_{token}",
            first_name="Review",
        )
        product = Product(
            sku=f"REVIEW-{token}",
            title="Payment review smoke product",
            slug=f"payment-review-smoke-{token}",
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
            sku=f"REVIEW-V-{token}",
            stock_qty=5,
            reserved_qty=0,
        )
        promo = PromoCode(
            code=f"REVIEW{token}".upper(),
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
            segment="review-smoke",
            loyalty_points=500,
        )
        db.add(profile)
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
            payment_id = f"review-{token}-{order_id}-{attempt}"
            provider_payments[payment_id] = {
                "id": payment_id,
                "status": "pending",
                "metadata": {"order_id": str(order_id)},
                "amount": {
                    "value": f"{_money(amount):.2f}",
                    "currency": currency,
                },
                "confirmation": {
                    "confirmation_url": f"https://pay.test/{payment_id}",
                },
            }
            return {
                "provider_payment_id": payment_id,
                "status": "pending",
                "confirmation_url": f"https://pay.test/{payment_id}",
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
        assert _money(cart["final_amount"]) == Decimal("1700.00")

        checkout_key = f"review-checkout-{token}"
        checkout_payload = {
            "name": "Review Customer",
            "phone": "+46701234567",
            "delivery_type": "pickup",
            "address": "",
            "comment": "Late provider success CI smoke",
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
        order_id = int(order["id"])
        assert order["status"] == "created"
        assert order["payment_status"] == "pending"
        assert _money(order["total_amount"]) == Decimal("1700.00")

        payment = _expect(
            client.post("/api/payments", json={"order_id": order_id}),
            200,
            "create pending payment",
        )
        provider_payment_id = payment["provider_payment_id"]
        assert payment["status"] == "pending"
        assert payment["confirmation_url"]

        provider_payments[provider_payment_id]["status"] = "canceled"
        canceled_payload = {
            "event": "payment.canceled",
            "object": {
                "id": provider_payment_id,
                "status": "canceled",
            },
        }
        canceled = _expect(
            client.post(
                "/api/payments/webhook/yookassa",
                json=canceled_payload,
            ),
            200,
            "provider cancellation",
        )
        assert canceled == {"ok": True}
        canceled_replay = _expect(
            client.post(
                "/api/payments/webhook/yookassa",
                json=canceled_payload,
            ),
            200,
            "idempotent cancellation webhook replay",
        )
        assert canceled_replay == {"ok": True, "idempotent": True}

        provider_payments[provider_payment_id]["status"] = "succeeded"
        succeeded_payload = {
            "event": "payment.succeeded",
            "object": {
                "id": provider_payment_id,
                "status": "succeeded",
            },
        }
        succeeded = _expect(
            client.post(
                "/api/payments/webhook/yookassa",
                json=succeeded_payload,
            ),
            200,
            "late provider success",
        )
        assert succeeded == {"ok": True}
        succeeded_replay = _expect(
            client.post(
                "/api/payments/webhook/yookassa",
                json=succeeded_payload,
            ),
            200,
            "idempotent late success webhook replay",
        )
        assert succeeded_replay == {"ok": True, "idempotent": True}

        db.expire_all()
        persisted_order = db.query(Order).filter(Order.id == order_id).one()
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        persisted_promo = db.query(PromoCode).filter(PromoCode.id == promo.id).one()
        persisted_profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).one()
        persisted_cart = db.query(Cart).filter(Cart.customer_id == customer.id).one()
        persisted_payment = (
            db.query(Payment)
            .filter(Payment.provider_payment_id == provider_payment_id)
            .one()
        )
        persisted_hold = (
            db.query(LoyaltyRedemptionHold)
            .filter(LoyaltyRedemptionHold.order_id == order_id)
            .one()
        )
        reconciliation_rows = (
            db.query(PaymentReconciliation)
            .filter(PaymentReconciliation.order_id == order_id)
            .all()
        )
        review_events = (
            db.query(BusinessEvent)
            .filter(
                BusinessEvent.event_type == "payment.review_required",
                BusinessEvent.aggregate_id == str(order_id),
            )
            .all()
        )
        notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .all()
        )
        cancellation_keys = (
            db.query(NotificationEventKey)
            .filter(
                NotificationEventKey.event_key
                == f"order:{order_id}:status:cancelled:delivery:cancelled"
            )
            .all()
        )
        paid_keys = (
            db.query(NotificationEventKey)
            .filter(NotificationEventKey.event_key == f"order:{order_id}:paid")
            .all()
        )
        payment_events = (
            db.query(PaymentEvent)
            .filter(PaymentEvent.provider_payment_id == provider_payment_id)
            .order_by(PaymentEvent.event_type.asc())
            .all()
        )

        assert persisted_order.status == "payment_review_required"
        assert persisted_order.payment_status == "paid_review_required"
        assert persisted_order.delivery_status == "cancelled"
        assert persisted_payment.status == "succeeded"
        assert persisted_variant.stock_qty == 5
        assert persisted_variant.reserved_qty == 0
        assert persisted_promo.used_count == 0
        assert persisted_cart.status == "converted"
        assert persisted_hold.status == "released"
        assert persisted_hold.released_at is not None
        assert _money(persisted_hold.points) == Decimal("100.00")
        assert _money(persisted_profile.loyalty_points) == Decimal("500.00")

        assert len(reconciliation_rows) == 1
        reconciliation = reconciliation_rows[0]
        assert reconciliation.payment_id == persisted_payment.id
        assert reconciliation.provider_payment_id == provider_payment_id
        assert reconciliation.status == "open"
        assert reconciliation.local_status == "paid_review_required"
        assert reconciliation.provider_status == "succeeded"
        assert reconciliation.message == "payment.review_required:paid_after_cancel"
        assert _money(reconciliation.amount_local) == Decimal("1700.00")
        assert _money(reconciliation.amount_provider) == Decimal("1700.00")

        assert len(review_events) == 1
        assert len(payment_events) == 2
        assert {event.event_type for event in payment_events} == {
            "payment.canceled",
            "payment.succeeded",
        }
        assert all(event.processed for event in payment_events)
        assert len(notifications) == 1
        assert len(cancellation_keys) == 1
        assert len(paid_keys) == 0
        assert db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order_id).count() == 0
        assert db.query(LoyaltyTransaction).filter(LoyaltyTransaction.order_id == order_id).count() == 0
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
                    "payment_status": persisted_order.payment_status,
                    "review_cases": len(reconciliation_rows),
                    "stock": persisted_variant.stock_qty,
                    "reserved": persisted_variant.reserved_qty,
                    "loyalty": f"{_money(persisted_profile.loyalty_points):.2f}",
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
