#!/usr/bin/env python3
"""Exercise partial then full cumulative refunds through real HTTP and PostgreSQL.

All application state is created in an outer transaction and rolled back. Only
external YooKassa payment/refund calls are replaced with deterministic fakes;
FastAPI routes, authorization dependencies, persistence, loyalty, inventory,
and refund state transitions are real.
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


def _expect_error(response, status_code: int, step: str) -> dict:
    payload = _expect(response, status_code, step)
    if "detail" not in payload:
        raise AssertionError(f"{step} returned no error detail: {payload!r}")
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
        profile = CrmProfile(
            customer_id=customer.id,
            segment="refund-smoke",
            loyalty_points=500,
        )
        db.add(profile)
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
            try:
                return provider_payments[payment_id]
            except KeyError as exc:
                raise AssertionError(
                    f"Unexpected provider payment lookup: {payment_id}"
                ) from exc

        async def fake_create_yookassa_refund(
            payment_id: str,
            amount: float,
            currency: str,
            order_id: int,
            return_id: int,
        ) -> dict:
            normalized_amount = _money(amount)
            call = (payment_id, normalized_amount, currency, order_id, return_id)
            refund_create_calls.append(call)
            refund_id = f"refund-{token}-{return_id}"
            expected = {
                "id": refund_id,
                "status": "succeeded",
                "payment_id": payment_id,
                "amount": {
                    "value": f"{normalized_amount:.2f}",
                    "currency": currency,
                },
            }
            existing = provider_refunds.get(refund_id)
            if existing and existing != expected:
                raise AssertionError("Provider refund idempotency payload changed")
            provider_refunds[refund_id] = expected
            return {
                "refund_id": refund_id,
                "status": "succeeded",
                "amount": expected["amount"],
            }

        async def fake_fetch_yookassa_refund(refund_id: str) -> dict:
            try:
                return provider_refunds[refund_id]
            except KeyError as exc:
                raise AssertionError(
                    f"Unexpected provider refund lookup: {refund_id}"
                ) from exc

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_customer] = override_customer
        app.dependency_overrides[get_current_admin] = override_admin
        payments_api.create_yookassa_payment = fake_create_yookassa_payment
        payments_api.fetch_yookassa_payment = fake_fetch_yookassa_payment
        returns_api.create_yookassa_refund = fake_create_yookassa_refund
        returns_api.fetch_yookassa_refund = fake_fetch_yookassa_refund
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

        checkout_key = f"refund-checkout-{token}"
        order = _expect(
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
        order_id = int(order["id"])
        assert _money(order["total_amount"]) == Decimal("1700.00")

        payment = _expect(
            client.post("/api/payments", json={"order_id": order_id}),
            200,
            "create and settle payment",
        )
        assert payment["status"] == "succeeded"
        provider_payment_id = payment["provider_payment_id"]

        first_return = _expect(
            client.post(
                "/api/returns",
                json={
                    "order_id": order_id,
                    "reason": "Partial refund smoke request",
                },
            ),
            200,
            "create first return",
        )
        first_return_id = int(first_return["id"])
        assert first_return["status"] == "requested"

        first_refund = _expect(
            client.post(
                "/api/returns/admin/approve",
                json={"return_id": first_return_id, "amount": 700},
            ),
            200,
            "approve partial refund",
        )
        assert first_refund["status"] == "succeeded"
        assert first_refund["return_status"] == "approved_partial"
        assert _money(first_refund["refund_amount"]) == Decimal("700.00")
        assert first_refund["idempotent"] is False

        first_refund_replay = _expect(
            client.post(
                "/api/returns/admin/approve",
                json={"return_id": first_return_id, "amount": 700},
            ),
            200,
            "replay partial refund approval",
        )
        assert first_refund_replay["idempotent"] is True
        assert len(refund_create_calls) == 1

        db.expire_all()
        partial_order = db.query(Order).filter(Order.id == order_id).one()
        partial_profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).one()
        partial_hold = (
            db.query(LoyaltyRedemptionHold)
            .filter(LoyaltyRedemptionHold.order_id == order_id)
            .one()
        )
        partial_loyalty = (
            db.query(LoyaltyTransaction)
            .filter(LoyaltyTransaction.order_id == order_id)
            .order_by(LoyaltyTransaction.id.asc())
            .all()
        )
        assert partial_order.status == "partially_refunded"
        assert partial_order.payment_status == "partially_refunded"
        assert _money(partial_profile.loyalty_points) == Decimal("417.00")
        assert partial_hold.status == "committed"
        assert [(row.reason, _money(row.points_delta)) for row in partial_loyalty] == [
            ("loyalty_redeemed", Decimal("-100.00")),
            ("order_paid", Decimal("17.00")),
        ]
        assert remaining_refundable_amount(db, partial_order) == Decimal("1000.00")

        second_return = _expect(
            client.post(
                "/api/returns",
                json={
                    "order_id": order_id,
                    "reason": "Refund the remaining balance",
                },
            ),
            200,
            "create second return",
        )
        second_return_id = int(second_return["id"])
        assert second_return["status"] == "requested"

        over_refund = _expect_error(
            client.post(
                "/api/returns/admin/approve",
                json={"return_id": second_return_id, "amount": 1000.01},
            ),
            409,
            "reject amount above remaining balance",
        )
        assert "remaining refundable balance" in str(over_refund["detail"])
        assert len(refund_create_calls) == 1

        second_refund = _expect(
            client.post(
                "/api/returns/admin/approve",
                json={"return_id": second_return_id, "amount": 1000},
            ),
            200,
            "approve remaining refund",
        )
        assert second_refund["status"] == "succeeded"
        assert second_refund["return_status"] == "approved"
        assert _money(second_refund["refund_amount"]) == Decimal("1000.00")
        assert second_refund["idempotent"] is False

        second_refund_replay = _expect(
            client.post(
                "/api/returns/admin/approve",
                json={"return_id": second_return_id, "amount": 1000},
            ),
            200,
            "replay full cumulative refund approval",
        )
        assert second_refund_replay["idempotent"] is True
        assert len(refund_create_calls) == 2
        assert len(provider_refunds) == 2

        third_return = _expect_error(
            client.post(
                "/api/returns",
                json={
                    "order_id": order_id,
                    "reason": "No balance should remain",
                },
            ),
            409,
            "reject return after full refund",
        )
        assert "Only paid orders" in str(third_return["detail"])

        db.expire_all()
        persisted_order = db.query(Order).filter(Order.id == order_id).one()
        persisted_payment = (
            db.query(Payment)
            .filter(Payment.provider_payment_id == provider_payment_id)
            .one()
        )
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        persisted_promo = db.query(PromoCode).filter(PromoCode.id == promo.id).one()
        persisted_profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).one()
        persisted_cart = db.query(Cart).filter(Cart.customer_id == customer.id).one()
        persisted_hold = (
            db.query(LoyaltyRedemptionHold)
            .filter(LoyaltyRedemptionHold.order_id == order_id)
            .one()
        )
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
        notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .all()
        )
        paid_keys = (
            db.query(NotificationEventKey)
            .filter(NotificationEventKey.event_key == f"order:{order_id}:paid")
            .all()
        )

        assert persisted_order.status == "refunded"
        assert persisted_order.payment_status == "refunded"
        assert persisted_payment.status == "succeeded"
        assert persisted_variant.stock_qty == 3
        assert persisted_variant.reserved_qty == 0
        assert persisted_promo.used_count == 1
        assert persisted_cart.status == "converted"
        assert persisted_hold.status == "refunded"
        assert persisted_hold.released_at is not None
        assert _money(persisted_hold.points) == Decimal("100.00")
        assert _money(persisted_profile.loyalty_points) == Decimal("500.00")

        assert len(returns) == 2
        assert returns[0].status == "approved_partial"
        assert _money(returns[0].refund_amount) == Decimal("700.00")
        assert returns[1].status == "approved"
        assert _money(returns[1].refund_amount) == Decimal("1000.00")
        assert sum((_money(row.refund_amount) for row in returns), Decimal("0.00")) == Decimal("1700.00")
        assert remaining_refundable_amount(db, persisted_order) == Decimal("0.00")

        assert [(row.reason, _money(row.points_delta)) for row in loyalty_rows] == [
            ("loyalty_redeemed", Decimal("-100.00")),
            ("order_paid", Decimal("17.00")),
            ("order_refund_reversal", Decimal("-17.00")),
            ("loyalty_refund", Decimal("100.00")),
        ]
        assert len(notifications) == 1
        assert len(paid_keys) == 1
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
        assert {call[1] for call in refund_create_calls} == {
            Decimal("700.00"),
            Decimal("1000.00"),
        }
        assert all(call[0] == provider_payment_id for call in refund_create_calls)
        assert all(call[2] == "RUB" for call in refund_create_calls)
        assert all(call[3] == order_id for call in refund_create_calls)
        assert {call[4] for call in refund_create_calls} == {
            first_return_id,
            second_return_id,
        }

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "refunds": ["700.00", "1000.00"],
                    "remaining": "0.00",
                    "loyalty_after_full_refund": f"{_money(persisted_profile.loyalty_points):.2f}",
                    "stock_after_financial_refund": persisted_variant.stock_qty,
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
