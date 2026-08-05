#!/usr/bin/env python3
"""Prove referral attribution and first-paid-order reward against PostgreSQL.

CI runs this script after Alembic migrations. Application HTTP routes, checkout,
reservation, payment webhook settlement, referral attribution, loyalty ledger,
fulfillment, notification and outbox code are real. Only the external YooKassa
HTTP boundary is replaced with a deterministic in-memory provider. All records
are contained in one outer transaction and rolled back after the assertions.
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
from backend.database import engine, get_db
from backend.main import app
from backend.models import (
    CrmProfile,
    Customer,
    LoyaltyTransaction,
    Order,
    PaymentEvent,
    Product,
    ProductVariant,
    ReferralAttribution,
    ReferralCode,
)
from backend.security import get_current_customer


def _money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _object(response, status_code: int, step: str) -> dict:
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
        inviter = Customer(
            telegram_id=str(int(token[:10], 16)),
            username=f"referrer_{token}",
            first_name="Referrer",
        )
        invited = Customer(
            telegram_id=str(int(token[10:], 16) + 10_000_000_000),
            username=f"invited_{token}",
            first_name="Invited",
        )
        product = Product(
            sku=f"REF-{token}",
            title="Referral smoke product",
            slug=f"referral-smoke-{token}",
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
            sku=f"REF-V-{token}",
            stock_qty=5,
            reserved_qty=0,
        )
        db.add_all([inviter, invited, product, variant])
        db.flush()
        referral = ReferralCode(
            customer_id=inviter.id,
            code=f"FL{token[:8]}".upper(),
            reward_points=250.0,
            used_count=0,
            active=True,
        )
        inviter_profile = CrmProfile(
            customer_id=inviter.id,
            segment="referrer",
            loyalty_points=0,
        )
        db.add_all([referral, inviter_profile])
        db.commit()

        def override_db():
            yield db

        def override_customer():
            return invited

        async def fake_create_yookassa_payment(
            order_id: int,
            amount: float,
            currency: str,
            *,
            attempt: int,
        ) -> dict:
            payment_id = f"referral-{token}-{order_id}-{attempt}"
            provider_payments[payment_id] = {
                "id": payment_id,
                "status": "pending",
                "metadata": {"order_id": str(order_id)},
                "amount": {
                    "value": f"{_money(amount):.2f}",
                    "currency": currency,
                },
                "confirmation": {
                    "confirmation_url": f"https://payments.test/{payment_id}",
                },
            }
            return {
                "provider_payment_id": payment_id,
                "status": "pending",
                "confirmation_url": f"https://payments.test/{payment_id}",
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

        def add_product_to_cart() -> dict:
            return _object(
                client.post(
                    "/api/cart/items",
                    json={
                        "product_id": product.id,
                        "variant_id": variant.id,
                        "quantity": 1,
                    },
                ),
                200,
                "add referral product to cart",
            )

        def checkout(idempotency_key: str, comment: str) -> dict:
            return _object(
                client.post(
                    "/api/orders/checkout",
                    headers={"Idempotency-Key": idempotency_key},
                    json={
                        "name": "Referral Smoke Customer",
                        "phone": "+46701234567",
                        "delivery_type": "pickup",
                        "address": "",
                        "comment": comment,
                    },
                ),
                200,
                "referral checkout",
            )

        def settle(order_id: int) -> str:
            payment = _object(
                client.post("/api/payments", json={"order_id": order_id}),
                200,
                "create referral payment",
            )
            assert payment["status"] == "pending"
            payment_id = str(payment["provider_payment_id"])
            provider_payments[payment_id]["status"] = "succeeded"
            provider_payments[payment_id]["confirmation"] = {}
            webhook_payload = {
                "event": "payment.succeeded",
                "object": {"id": payment_id, "status": "succeeded"},
            }
            first = _object(
                client.post("/api/payments/webhook/yookassa", json=webhook_payload),
                200,
                "first referral payment webhook",
            )
            assert first["ok"] is True
            replay = _object(
                client.post("/api/payments/webhook/yookassa", json=webhook_payload),
                200,
                "duplicate referral payment webhook",
            )
            assert replay == {"ok": True, "idempotent": True}
            return payment_id

        first_cart = add_product_to_cart()
        assert _money(first_cart["final_amount"]) == Decimal("1000.00")
        _object(
            client.post("/api/cart/referral", json={"code": referral.code.lower()}),
            200,
            "attach referral before first purchase",
        )
        _object(
            client.post("/api/cart/referral", json={"code": referral.code}),
            200,
            "idempotent referral attachment",
        )

        first_order = checkout(f"referral-first-{token}", "First referred purchase")
        first_order_id = int(first_order["id"])
        first_payment_id = settle(first_order_id)

        db.expire_all()
        first_persisted_order = db.query(Order).filter(Order.id == first_order_id).one()
        persisted_attribution = (
            db.query(ReferralAttribution)
            .filter(ReferralAttribution.invited_customer_id == invited.id)
            .one()
        )
        persisted_referral = db.query(ReferralCode).filter(ReferralCode.id == referral.id).one()
        persisted_inviter_profile = (
            db.query(CrmProfile)
            .filter(CrmProfile.customer_id == inviter.id)
            .one()
        )
        reward_rows = (
            db.query(LoyaltyTransaction)
            .filter(
                LoyaltyTransaction.customer_id == inviter.id,
                LoyaltyTransaction.reason == "referral_reward",
            )
            .all()
        )

        assert first_persisted_order.status == "paid"
        assert first_persisted_order.payment_status == "paid"
        assert first_persisted_order.referral_code == referral.code
        assert persisted_attribution.status == "rewarded"
        assert persisted_attribution.rewarded_order_id == first_order_id
        assert persisted_attribution.rewarded_at is not None
        assert persisted_referral.used_count == 1
        assert _money(persisted_inviter_profile.loyalty_points) == Decimal("250.00")
        assert len(reward_rows) == 1
        assert reward_rows[0].order_id == first_order_id
        assert _money(reward_rows[0].points_delta) == Decimal("250.00")

        second_cart = add_product_to_cart()
        assert _money(second_cart["final_amount"]) == Decimal("1000.00")
        late_referral = client.post(
            "/api/cart/referral",
            json={"code": referral.code},
        )
        assert late_referral.status_code == 409
        assert late_referral.json() == {
            "detail": "Referral code must be applied before the first paid order"
        }

        second_order = checkout(f"referral-second-{token}", "Second purchase")
        second_order_id = int(second_order["id"])
        second_payment_id = settle(second_order_id)

        db.expire_all()
        second_persisted_order = db.query(Order).filter(Order.id == second_order_id).one()
        persisted_referral = db.query(ReferralCode).filter(ReferralCode.id == referral.id).one()
        persisted_inviter_profile = (
            db.query(CrmProfile)
            .filter(CrmProfile.customer_id == inviter.id)
            .one()
        )
        reward_rows = (
            db.query(LoyaltyTransaction)
            .filter(
                LoyaltyTransaction.customer_id == inviter.id,
                LoyaltyTransaction.reason == "referral_reward",
            )
            .all()
        )

        assert second_persisted_order.status == "paid"
        assert second_persisted_order.referral_code is None
        assert persisted_referral.used_count == 1
        assert _money(persisted_inviter_profile.loyalty_points) == Decimal("250.00")
        assert len(reward_rows) == 1
        assert db.query(PaymentEvent).filter(
            PaymentEvent.provider_payment_id.in_([first_payment_id, second_payment_id]),
            PaymentEvent.event_type == "payment.succeeded",
        ).count() == 2

        print(
            json.dumps(
                {
                    "status": "ok",
                    "inviter_id": inviter.id,
                    "invited_customer_id": invited.id,
                    "first_order_id": first_order_id,
                    "second_order_id": second_order_id,
                    "attribution_status": persisted_attribution.status,
                    "referral_rewards": len(reward_rows),
                    "referral_used_count": persisted_referral.used_count,
                    "inviter_points": f"{_money(persisted_inviter_profile.loyalty_points):.2f}",
                    "late_referral_rejected": True,
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
