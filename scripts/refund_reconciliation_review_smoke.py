#!/usr/bin/env python3
"""Prove malformed provider refunds become durable, non-repeating reviews."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import engine
from backend.jobs import refund_jobs
from backend.models import Customer, Order, ReturnRequest


def _provider_refund(refund_id: str, value: str, currency: str) -> dict:
    return {
        "id": refund_id,
        "status": "succeeded",
        "amount": {
            "value": value,
            "currency": currency,
        },
    }


def main() -> int:
    token = uuid.uuid4().hex[:20]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    original_fetch = refund_jobs.fetch_yookassa_refund
    provider_calls: list[str] = []

    try:
        customer = Customer(
            telegram_id=str(int(token, 16)),
            username=f"refund_review_{token}",
            first_name="Refund Review",
        )
        db.add(customer)
        db.flush()

        currency_order = Order(
            customer_id=customer.id,
            status="refund_requested",
            payment_status="refund_pending",
            delivery_status="delivered",
            total_amount=1000.0,
            currency="RUB",
        )
        amount_order = Order(
            customer_id=customer.id,
            status="refund_requested",
            payment_status="refund_pending",
            delivery_status="delivered",
            total_amount=1000.0,
            currency="RUB",
        )
        valid_order = Order(
            customer_id=customer.id,
            status="refund_requested",
            payment_status="refund_pending",
            delivery_status="delivered",
            total_amount=1000.0,
            currency="RUB",
        )
        db.add_all([currency_order, amount_order, valid_order])
        db.flush()

        currency_return = ReturnRequest(
            order_id=currency_order.id,
            customer_id=customer.id,
            reason="Provider currency mismatch smoke",
            status="refund_pending",
            provider_refund_id=f"refund-currency-{token}",
            refund_amount=500.0,
        )
        amount_return = ReturnRequest(
            order_id=amount_order.id,
            customer_id=customer.id,
            reason="Provider amount mismatch smoke",
            status="refund_pending",
            provider_refund_id=f"refund-amount-{token}",
            refund_amount=500.0,
        )
        valid_return = ReturnRequest(
            order_id=valid_order.id,
            customer_id=customer.id,
            reason="Valid provider refund smoke",
            status="refund_pending",
            provider_refund_id=f"refund-valid-{token}",
            refund_amount=400.0,
        )
        db.add_all([currency_return, amount_return, valid_return])
        db.commit()

        provider_payloads = {
            currency_return.provider_refund_id: _provider_refund(
                currency_return.provider_refund_id,
                "500.00",
                "USD",
            ),
            amount_return.provider_refund_id: _provider_refund(
                amount_return.provider_refund_id,
                "600.00",
                "RUB",
            ),
            valid_return.provider_refund_id: _provider_refund(
                valid_return.provider_refund_id,
                "400.00",
                "RUB",
            ),
        }

        async def fake_fetch_yookassa_refund(refund_id: str) -> dict:
            provider_calls.append(refund_id)
            return provider_payloads[refund_id]

        refund_jobs.fetch_yookassa_refund = fake_fetch_yookassa_refund

        first = asyncio.run(refund_jobs.reconcile_pending_refunds(db, limit=50))
        assert first == {
            "seen": 3,
            "succeeded": 1,
            "pending": 0,
            "canceled": 0,
            "review_required": 2,
            "provider_errors": 0,
            "skipped": 0,
        }

        db.expire_all()
        persisted_currency_return = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.id == currency_return.id)
            .one()
        )
        persisted_amount_return = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.id == amount_return.id)
            .one()
        )
        persisted_valid_return = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.id == valid_return.id)
            .one()
        )
        persisted_currency_order = (
            db.query(Order).filter(Order.id == currency_order.id).one()
        )
        persisted_amount_order = (
            db.query(Order).filter(Order.id == amount_order.id).one()
        )
        persisted_valid_order = db.query(Order).filter(Order.id == valid_order.id).one()

        assert persisted_currency_return.status == "refund_review_required"
        assert persisted_amount_return.status == "refund_review_required"
        assert persisted_currency_order.status == "refund_requested"
        assert persisted_amount_order.status == "refund_requested"
        assert persisted_currency_order.payment_status == "refund_review_required"
        assert persisted_amount_order.payment_status == "refund_review_required"

        assert persisted_valid_return.status == "approved_partial"
        assert persisted_valid_order.status == "partially_refunded"
        assert persisted_valid_order.payment_status == "partially_refunded"

        assert sorted(provider_calls) == sorted(provider_payloads)
        second = asyncio.run(refund_jobs.reconcile_pending_refunds(db, limit=50))
        assert second == {
            "seen": 0,
            "succeeded": 0,
            "pending": 0,
            "canceled": 0,
            "review_required": 0,
            "provider_errors": 0,
            "skipped": 0,
        }
        assert sorted(provider_calls) == sorted(provider_payloads)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "review_returns": [
                        persisted_currency_return.id,
                        persisted_amount_return.id,
                    ],
                    "valid_return": persisted_valid_return.id,
                    "provider_calls": len(provider_calls),
                    "automatic_rechecks_after_review": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        refund_jobs.fetch_yookassa_refund = original_fetch
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
