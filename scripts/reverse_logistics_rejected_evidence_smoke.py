#!/usr/bin/env python3
"""Prove rejected physical mutations cannot consume idempotency or persist evidence."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine
from backend.models import Customer, Order, OrderItem, Product, ProductVariant, ReturnRequest
from backend.reverse_logistics_models import ReturnLogisticsEvent, ReturnLogisticsItem
from backend.services.reverse_logistics import (
    authorize_item,
    ensure_physical_case,
    inspect_item,
    mark_in_transit,
    receive_item,
)


def _expect_409(operation, fragment: str) -> None:
    try:
        operation()
    except HTTPException as exc:
        assert exc.status_code == 409, exc
        assert fragment.lower() in str(exc.detail).lower(), exc.detail
    else:
        raise AssertionError("expected HTTP 409 conflict")


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("rejected evidence smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:12]
    with SessionLocal() as db:
        customer = Customer(telegram_id=f"rejected-evidence-{token}", first_name="Rejected evidence")
        product = Product(
            sku=f"REJ-P-{token}",
            slug=f"rejected-evidence-{token}",
            title="Rejected evidence item",
            price=100,
            currency="RUB",
            active=True,
        )
        variant = ProductVariant(
            product=product,
            size="M",
            color="Black",
            sku=f"REJ-V-{token}",
            stock_qty=10,
            reserved_qty=0,
        )
        db.add_all([customer, product, variant])
        db.flush()

        order = Order(
            customer_id=customer.id,
            status="completed",
            payment_status="paid",
            delivery_status="delivered",
            total_amount=200,
            delivery_price=0,
            discount_amount=0,
            loyalty_points_redeemed=0,
            loyalty_discount_amount=0,
            currency="RUB",
            delivery_type="pickup",
            address="Самовывоз",
        )
        db.add(order)
        db.flush()
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            title=product.title,
            size=variant.size,
            quantity=2,
            price=100,
        )
        ret = ReturnRequest(
            order_id=order.id,
            customer_id=customer.id,
            reason="Rejected evidence transactional smoke",
            status="approved",
            provider_refund_id=f"refund-rejected-{token}",
            refund_amount=200,
        )
        db.add_all([order_item, ret])
        db.commit()

        order = db.query(Order).filter(Order.id == order.id).one()
        ret = db.query(ReturnRequest).filter(ReturnRequest.id == ret.id).one()
        case = ensure_physical_case(db, ret=ret, order=order)
        item = (
            db.query(ReturnLogisticsItem)
            .filter(ReturnLogisticsItem.case_id == case.id, ReturnLogisticsItem.order_item_id == order_item.id)
            .one()
        )
        authorize_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=2,
            idempotency_key=f"authorize-{token}",
            actor_admin_id=None,
        )
        db.commit()

        early_receive_key = f"early-receive-{token}"
        _expect_409(
            lambda: receive_item(
                db,
                case_id=case.id,
                item_id=item.id,
                quantity=1,
                idempotency_key=early_receive_key,
                actor_admin_id=None,
            ),
            "in transit",
        )
        # Deliberately do NOT rollback. A rejected service mutation must leave no
        # durable/transient event in the caller's still-usable transaction.
        assert (
            db.query(ReturnLogisticsEvent)
            .filter(
                ReturnLogisticsEvent.case_id == case.id,
                ReturnLogisticsEvent.idempotency_key == early_receive_key,
            )
            .count()
            == 0
        )
        db.commit()

        mark_in_transit(db, case_id=case.id)
        db.commit()
        receive_after_transition = receive_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=1,
            idempotency_key=early_receive_key,
            actor_admin_id=None,
        )
        assert receive_after_transition.idempotent is False
        db.commit()

        early_inspect_key = f"early-inspect-{token}"
        _expect_409(
            lambda: inspect_item(
                db,
                case_id=case.id,
                item_id=item.id,
                quantity=1,
                disposition="resalable",
                idempotency_key=early_inspect_key,
                actor_admin_id=None,
            ),
            "fully received",
        )
        assert (
            db.query(ReturnLogisticsEvent)
            .filter(
                ReturnLogisticsEvent.case_id == case.id,
                ReturnLogisticsEvent.idempotency_key == early_inspect_key,
            )
            .count()
            == 0
        )
        db.commit()

        receive_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=1,
            idempotency_key=f"receive-rest-{token}",
            actor_admin_id=None,
        )
        db.commit()

        inspect_after_transition = inspect_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=1,
            disposition="resalable",
            idempotency_key=early_inspect_key,
            actor_admin_id=None,
        )
        assert inspect_after_transition.idempotent is False
        db.commit()

        events = {
            event.idempotency_key: event.event_type
            for event in db.query(ReturnLogisticsEvent).filter(ReturnLogisticsEvent.case_id == case.id).all()
        }
        assert events[early_receive_key] == "received"
        assert events[early_inspect_key] == "inspected"

    print({
        "status": "ok",
        "rejected_receive_event_absent_without_rollback": True,
        "rejected_inspect_event_absent_without_rollback": True,
        "rejected_keys_reusable_after_valid_transition": True,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
