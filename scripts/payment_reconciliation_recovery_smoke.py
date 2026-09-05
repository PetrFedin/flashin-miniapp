#!/usr/bin/env python3
"""Prove recovery of a succeeded YooKassa payment when its callback is missed.

The HTTP checkout/payment path, PostgreSQL persistence, inventory settlement,
fulfillment creation, notification enqueue and MoySklad ProviderCommand enqueue
are real. Only YooKassa network I/O is replaced with a deterministic local fake.
No payment webhook is sent and no PaymentEvent row is fabricated.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import payments as payments_api
from backend.database import engine, get_db
from backend.jobs.payment_jobs import reconcile_pending_payments
from backend.main import app
from backend.models import (
    Customer,
    FulfillmentTask,
    Notification,
    Order,
    Payment,
    PaymentEvent,
    Product,
    ProductVariant,
)
from backend.notification_models import NotificationEventKey
from backend.provider_models import ProviderCommand
from backend.security import get_current_customer
from backend.services import moysklad_outbound


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
    original_moysklad_settings = moysklad_outbound.get_settings
    client: TestClient | None = None
    provider_payments: dict[str, dict] = {}

    try:
        customer = Customer(
            telegram_id=str(int(token, 16)),
            username=f"payment_recovery_{token}",
            first_name="Recovery",
        )
        product = Product(
            sku=f"PAYREC-{token}",
            title="Payment reconciliation recovery product",
            slug=f"payment-reconciliation-recovery-{token}",
            brand="FLASHIN",
            price=1250.0,
            currency="RUB",
            category="Testing",
            gender="unisex",
            active=True,
        )
        variant = ProductVariant(
            product=product,
            size="M",
            color="Black",
            sku=f"PAYREC-V-{token}",
            stock_qty=5,
            reserved_qty=0,
        )
        db.add_all([customer, product, variant])
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
            payment_id = f"payment-recovery-{token}-{order_id}-{attempt}"
            confirmation_url = f"https://pay.test/{payment_id}"
            provider_payments[payment_id] = {
                "id": payment_id,
                "status": "pending",
                "metadata": {"order_id": str(order_id)},
                "amount": {
                    "value": f"{_money(amount):.2f}",
                    "currency": currency,
                },
                "confirmation": {"confirmation_url": confirmation_url},
            }
            return {
                "provider_payment_id": payment_id,
                "status": "pending",
                "confirmation_url": confirmation_url,
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
        moysklad_outbound.get_settings = lambda: SimpleNamespace(
            moysklad_order_export_enabled=True,
        )
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
        assert _money(cart["final_amount"]) == Decimal("2500.00")

        checkout_payload = {
            "name": "Recovery Customer",
            "phone": "+46701234567",
            "delivery_type": "pickup",
            "address": "",
            "comment": "Missed YooKassa callback recovery smoke",
        }
        order = _expect(
            client.post(
                "/api/orders/checkout",
                headers={"Idempotency-Key": f"payment-recovery-{token}"},
                json=checkout_payload,
            ),
            200,
            "checkout",
        )
        order_id = int(order["id"])
        assert order["status"] == "created"
        assert order["payment_status"] == "pending"

        payment = _expect(
            client.post("/api/payments", json={"order_id": order_id}),
            200,
            "create pending payment",
        )
        provider_payment_id = str(payment["provider_payment_id"])
        assert payment["status"] == "pending"
        assert payment["confirmation_url"]

        db.expire_all()
        reserved_variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == variant.id)
            .one()
        )
        assert reserved_variant.stock_qty == 5
        assert reserved_variant.reserved_qty == 2
        assert db.query(PaymentEvent).filter(PaymentEvent.provider_payment_id == provider_payment_id).count() == 0
        assert db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order_id).count() == 0
        assert db.query(Notification).filter(Notification.telegram_id == customer.telegram_id).count() == 0
        assert db.query(ProviderCommand).filter(ProviderCommand.aggregate_id == str(order_id)).count() == 0

        provider_payments[provider_payment_id]["status"] = "succeeded"
        provider_payments[provider_payment_id]["confirmation"] = {}

        outcome = asyncio.run(reconcile_pending_payments(db))
        assert outcome == {
            "seen": 1,
            "succeeded": 1,
            "pending": 0,
            "canceled": 0,
            "review_required": 0,
            "provider_errors": 0,
            "skipped": 0,
        }

        db.expire_all()
        persisted_order = db.query(Order).filter(Order.id == order_id).one()
        persisted_payment = (
            db.query(Payment)
            .filter(Payment.provider_payment_id == provider_payment_id)
            .one()
        )
        persisted_variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == variant.id)
            .one()
        )
        paid_notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .all()
        )
        paid_event_keys = (
            db.query(NotificationEventKey)
            .filter(NotificationEventKey.event_key == f"order:{order_id}:paid")
            .all()
        )
        commands = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == "moysklad",
                ProviderCommand.aggregate_type == "order",
                ProviderCommand.aggregate_id == str(order_id),
            )
            .all()
        )

        assert persisted_order.status == "paid"
        assert persisted_order.payment_status == "paid"
        assert persisted_payment.status == "succeeded"
        assert persisted_payment.confirmation_url == ""
        assert persisted_variant.stock_qty == 3
        assert persisted_variant.reserved_qty == 0
        assert db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order_id).count() == 1
        assert len(paid_notifications) == 1
        assert len(paid_event_keys) == 1
        assert db.query(PaymentEvent).filter(PaymentEvent.provider_payment_id == provider_payment_id).count() == 0
        assert len(commands) == 1
        command = commands[0]
        assert command.command_type == "moysklad.customer_order.create"
        assert command.idempotency_key == f"order:{order_id}:customer_order:v1"
        assert command.status == "pending"

        replay = asyncio.run(reconcile_pending_payments(db))
        assert replay["seen"] == 0
        assert db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order_id).count() == 1
        assert db.query(Notification).filter(Notification.telegram_id == customer.telegram_id).count() == 1
        assert db.query(ProviderCommand).filter(ProviderCommand.aggregate_id == str(order_id)).count() == 1
        assert db.query(PaymentEvent).filter(PaymentEvent.provider_payment_id == provider_payment_id).count() == 0

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "payment_status": persisted_order.payment_status,
                    "stock_after_recovery": persisted_variant.stock_qty,
                    "fulfillment_tasks": 1,
                    "notifications": len(paid_notifications),
                    "moysklad_commands": len(commands),
                    "payment_events": 0,
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
        moysklad_outbound.get_settings = original_moysklad_settings
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
