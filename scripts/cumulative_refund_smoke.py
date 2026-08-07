#!/usr/bin/env python3
"""Exercise partial then full cumulative refunds through real HTTP and PostgreSQL.

All local application state uses real routes and transactional persistence. Only
YooKassa HTTP calls are replaced with deterministic fakes. The smoke proves
that partial refunds do not fabricate item-level stock returns, while the full
cumulative refund restores sold inventory exactly once and queues customer
notifications idempotently.
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
from backend.api import returns as returns_api
from backend.checkout_models import CheckoutAttempt
from backend.database import engine, get_db
from backend.main import app
from backend.models import (
    AdminUser,
    Cart,
    CrmProfile,
    Customer,
    FulfillmentTask,
    InventoryMovement,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Notification,
    Order,
    Payment,
    Product,
    ProductVariant,
    PromoCode,
    ReturnRequest,
)
from backend.notification_models import NotificationEventKey
from backend.security import get_current_admin, get_current_customer
from backend.services.refund_state import remaining_refundable_amount


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
    original_create_refund = returns_api.create_yookassa_refund
    original_fetch_refund = returns_api.fetch_yookassa_refund
    provider_payments: dict[str, dict] = {}
    provider_refunds: dict[str, dict] = {}
    refund_create_calls: list[tuple[str, Decimal, str, int, int]] = []
    client: TestClient | None = None

    try:
        customer = Customer(
            telegram_id=str(int(token, 16)),
            username=f"refund_{token}",
            first_name="Refund",
        )
        admin = AdminUser(
            email=f"refund-{token}@test.local",
            password_hash="not-used-by-dependency-override",
            role="manager",
            active=True,
        )
        product = Product(
            sku=f"REFUND-{token}",
            title="Cumulative refund smoke product",
            slug=f"cumulative-refund-smoke-{token}",
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
            sku=f"REFUND-V-{token}",
            stock_qty=5,
            reserved_qty=0,
        )
        promo = PromoCode(
            code=f"REFUND{token}".upper(),
            discount_type="percent",
            discount_value=10,
            min_amount=0,
            max_uses=1,
            used_count=0,
            active=True,
        )
        db.add_all([customer, admin, product, variant, promo])
        db.flush()
        db.add(
            CrmProfile(
                customer_id=customer.id,
                segment="refund-smoke",
                loyalty_points=500,
            )
        )
        db.commit()

        def override_db():
            yield db

        def override_customer():
            return customer

        def override_admin():
            return admin

        async def fake_create_yookassa_payment(
            order_id: int,
            amount: float,
            currency: str,
            *,
            attempt: int,
        ) -> dict:
            payment_id = f"refund-payment-{token}-{order_id}-{attempt}"
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
            return provider_payments[payment_id]

        async def fake_create_yookassa_refund(
            payment_id: str,
            amount: float,
            currency: str,
            order_id: int,
            return_id: int,
        ) -> dict:
            normalized_amount = _money(amount)
            refund_create_calls.append(
                (payment_id, normalized_amount, currency, order_id, return_id)
            )
            refund_id = f"refund-{token}-{return_id}"
            provider_refunds[refund_id] = {
                "id": refund_id,
                "status": "succeeded",
                "payment_id": payment_id,
                "amount": {
                    "value": f"{normalized_amount:.2f}",
                    "currency": currency,
                },
            }
            return {
                "refund_id": refund_id,
                "status": "succeeded",
                "amount": provider_refunds[refund_id]["amount"],
            }

        async def fake_fetch_yookassa_refund(refund_id: str) -> dict:
            return provider_refunds[refund_id]

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_customer] = override_customer
        app.dependency_overrides[get_current_admin] = override_admin
        payments_api.create_yookassa_payment = fake_create_yookassa_payment
        payments_api.fetch_yookassa_payment = fake_fetch_yookassa_payment
        returns_api.create_yookassa_refund = fake_create_yookassa_refund
        returns_api.fetch_yookassa_refund = fake_fetch_yookassa_refund
        client = TestClient(app)

        _expect(
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
        assert _money(cart["final_amount"]) == Decimal("1700.00")

        checkout_key = f"refund-checkout-{token}"
        order_payload = _expect(
            client.post(
                "/api/orders/checkout",
                headers={"Idempotency-Key": checkout_key},
                json={
                    "name": "Refund Customer",
                    "phone": "+46701234567",
                    "delivery_type": "pickup",
                    "address": "",
                    "comment": "Cumulative refund CI smoke",
                },
            ),
            200,
            "checkout",
        )
        order_id = int(order_payload["id"])
        assert _money(order_payload["total_amount"]) == Decimal("1700.00")

        payment_payload = _expect(
            client.post("/api/payments", json={"order_id": order_id}),
            200,
            "create and settle payment",
        )
        assert payment_payload["status"] == "succeeded"
        provider_payment_id = payment_payload["provider_payment_id"]
        db.expire_all()
        assert db.query(ProductVariant).filter(ProductVariant.id == variant.id).one().stock_qty == 3

        first_return = _expect(
            client.post(
                "/api/returns",
                json={"order_id": order_id, "reason": "Partial refund smoke request"},
            ),
            200,
            "create partial return",
        )
        first_return_id = int(first_return["id"])
        first_refund = _expect(
            client.post(
                "/api/returns/admin/approve",
                json={"return_id": first_return_id, "amount": 700},
            ),
            200,
            "approve partial refund",
        )
        assert first_refund["return_status"] == "approved_partial"
        db.expire_all()
        partial_order = db.query(Order).filter(Order.id == order_id).one()
        assert partial_order.payment_status == "partially_refunded"
        assert db.query(ProductVariant).filter(ProductVariant.id == variant.id).one().stock_qty == 3
        assert (
            db.query(InventoryMovement)
            .filter(InventoryMovement.order_id == order_id, InventoryMovement.kind == "return")
            .count()
            == 0
        )
        assert remaining_refundable_amount(db, partial_order) == Decimal("1000.00")

        second_return = _expect(
            client.post(
                "/api/returns",
                json={"order_id": order_id, "reason": "Refund the remaining balance"},
            ),
            200,
            "create final return",
        )
        second_return_id = int(second_return["id"])
        second_refund = _expect(
            client.post(
                "/api/returns/admin/approve",
                json={"return_id": second_return_id, "amount": 1000},
            ),
            200,
            "approve full cumulative refund",
        )
        assert second_refund["return_status"] == "approved"

        replay = _expect(
            client.post(
                "/api/returns/admin/approve",
                json={"return_id": second_return_id, "amount": 1000},
            ),
            200,
            "replay full cumulative refund",
        )
        assert replay["idempotent"] is True
        assert len(refund_create_calls) == 2

        db.expire_all()
        persisted_order = db.query(Order).filter(Order.id == order_id).one()
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        persisted_profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).one()
        persisted_hold = db.query(LoyaltyRedemptionHold).filter(LoyaltyRedemptionHold.order_id == order_id).one()
        persisted_cart = db.query(Cart).filter(Cart.customer_id == customer.id).one()
        returns = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.order_id == order_id)
            .order_by(ReturnRequest.id.asc())
            .all()
        )
        loyalty_rows = (
            db.query(LoyaltyTransaction)
            .filter(LoyaltyTransaction.order_id == order_id)
            .order_by(LoyaltyTransaction.id.asc())
            .all()
        )
        return_movements = (
            db.query(InventoryMovement)
            .filter(InventoryMovement.order_id == order_id, InventoryMovement.kind == "return")
            .all()
        )
        notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .all()
        )
        event_keys = (
            db.query(NotificationEventKey)
            .filter(NotificationEventKey.event_key.like(f"order:{order_id}:%"))
            .all()
        )

        assert persisted_order.status == "refunded"
        assert persisted_order.payment_status == "refunded"
        assert persisted_variant.stock_qty == 5
        assert persisted_variant.reserved_qty == 0
        assert persisted_cart.status == "converted"
        assert persisted_hold.status == "refunded"
        assert _money(persisted_profile.loyalty_points) == Decimal("500.00")
        assert len(returns) == 2
        assert [row.status for row in returns] == ["approved_partial", "approved"]
        assert remaining_refundable_amount(db, persisted_order) == Decimal("0.00")
        assert len(return_movements) == 1
        assert return_movements[0].quantity == 2
        assert return_movements[0].stock_before == 3
        assert return_movements[0].stock_after == 5
        assert [row.reason for row in loyalty_rows] == [
            "loyalty_redeemed",
            "order_paid",
            "order_refund_reversal",
            "loyalty_refund",
        ]
        assert len(notifications) == 3
        assert len(event_keys) == 3
        assert db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order_id).count() == 1
        assert db.query(Payment).filter(Payment.order_id == order_id).count() == 1
        assert (
            db.query(CheckoutAttempt)
            .filter(
                CheckoutAttempt.customer_id == customer.id,
                CheckoutAttempt.idempotency_key == checkout_key,
            )
            .count()
            == 1
        )
        assert all(call[0] == provider_payment_id for call in refund_create_calls)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "refunds": ["700.00", "1000.00"],
                    "remaining": "0.00",
                    "stock_after_full_refund": persisted_variant.stock_qty,
                    "return_movements": len(return_movements),
                    "notifications": len(notifications),
                    "provider_refund_calls": len(refund_create_calls),
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
        returns_api.create_yookassa_refund = original_create_refund
        returns_api.fetch_yookassa_refund = original_fetch_refund
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
