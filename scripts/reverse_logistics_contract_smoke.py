#!/usr/bin/env python3
"""Adversarial PostgreSQL proof for physical-return lifecycle and idempotency."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.reverse_logistics import _physical_payload
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


def _expect_conflict(operation, expected_fragment: str) -> None:
    try:
        operation()
    except HTTPException as exc:
        assert exc.status_code == 409, exc
        assert expected_fragment.lower() in str(exc.detail).lower(), exc.detail
    else:
        raise AssertionError("expected HTTP 409 conflict")


def _fixture(db, token: str):
    customer = Customer(telegram_id=f"reverse-contract-{token}", first_name="Contract")
    db.add(customer)
    db.flush()

    order = Order(
        customer_id=customer.id,
        status="completed",
        payment_status="paid",
        delivery_status="delivered",
        total_amount=400,
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

    order_items = []
    variants = []
    for index, quantity in enumerate((2, 1, 1), start=1):
        product = Product(
            sku=f"REV-CONTRACT-P-{token}-{index}",
            slug=f"rev-contract-{token}-{index}",
            title=f"Return line {index}",
            price=100,
            currency="RUB",
            active=True,
        )
        variant = ProductVariant(
            product=product,
            size=f"S{index}",
            color="Black",
            sku=f"REV-CONTRACT-V-{token}-{index}",
            stock_qty=10,
            reserved_qty=0,
        )
        db.add_all([product, variant])
        db.flush()
        item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            title=product.title,
            size=variant.size,
            quantity=quantity,
            price=100,
        )
        db.add(item)
        order_items.append(item)
        variants.append(variant)

    ret = ReturnRequest(
        order_id=order.id,
        customer_id=customer.id,
        reason="Physical contract smoke",
        status="approved",
        provider_refund_id=f"refund-{token}",
        refund_amount=300,
    )
    sibling_ret = ReturnRequest(
        order_id=order.id,
        customer_id=customer.id,
        reason="Second financial return for the same original order",
        status="approved_partial",
        provider_refund_id=f"refund-sibling-{token}",
        refund_amount=100,
    )
    db.add_all([ret, sibling_ret])
    db.commit()
    return (
        order.id,
        ret.id,
        sibling_ret.id,
        [item.id for item in order_items],
        [variant.id for variant in variants],
    )


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("reverse logistics contract smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:12]
    with SessionLocal() as db:
        order_id, return_id, sibling_return_id, order_item_ids, variant_ids = _fixture(db, token)

        ret = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).one()
        preview = _physical_payload(db, ret)
        assert preview["physical_status"] == "not_started"
        assert [row["order_item_id"] for row in preview["items"]] == order_item_ids
        assert [row["ordered_qty"] for row in preview["items"]] == [2, 1, 1]
        assert preview["items"][0]["title"] == "Return line 1"

        order = db.query(Order).filter(Order.id == order_id).one()
        case = ensure_physical_case(db, ret=ret, order=order)
        items = {
            int(row.order_item_id): row
            for row in db.query(ReturnLogisticsItem).filter(ReturnLogisticsItem.case_id == case.id).all()
        }

        shared_key = f"shared-{token}"
        authorize_item(
            db,
            case_id=case.id,
            item_id=items[order_item_ids[0]].id,
            quantity=2,
            idempotency_key=shared_key,
            actor_admin_id=None,
            reason="line one",
        )
        db.commit()

        # A second ReturnRequest for the same order must not create a second
        # inventory authority. Both physical cases share the original sold-line
        # quantity ceiling, even though their financial refund records differ.
        sibling_ret = db.query(ReturnRequest).filter(ReturnRequest.id == sibling_return_id).one()
        sibling_case = ensure_physical_case(
            db,
            ret=sibling_ret,
            order=db.query(Order).filter(Order.id == order_id).one(),
        )
        sibling_items = {
            int(row.order_item_id): row
            for row in db.query(ReturnLogisticsItem).filter(ReturnLogisticsItem.case_id == sibling_case.id).all()
        }
        db.commit()

        _expect_conflict(
            lambda: authorize_item(
                db,
                case_id=sibling_case.id,
                item_id=sibling_items[order_item_ids[0]].id,
                quantity=1,
                idempotency_key=f"sibling-overclaim-{token}",
                actor_admin_id=None,
                reason="must not authorize already claimed sold units",
            ),
            "original sold quantity",
        )
        db.rollback()
        db.expire_all()
        assert (
            db.query(ReturnLogisticsItem)
            .filter(
                ReturnLogisticsItem.case_id == sibling_case.id,
                ReturnLogisticsItem.order_item_id == order_item_ids[0],
            )
            .one()
            .authorized_qty
            == 0
        )

        _expect_conflict(
            lambda: authorize_item(
                db,
                case_id=case.id,
                item_id=items[order_item_ids[1]].id,
                quantity=1,
                idempotency_key=shared_key,
                actor_admin_id=None,
                reason="line two",
            ),
            "reused with different",
        )
        db.rollback()

        _expect_conflict(
            lambda: authorize_item(
                db,
                case_id=case.id,
                item_id=items[order_item_ids[0]].id,
                quantity=2,
                idempotency_key=shared_key,
                actor_admin_id=None,
                reason="changed reason",
            ),
            "reused with different",
        )
        db.rollback()

        case = ensure_physical_case(
            db,
            ret=db.query(ReturnRequest).filter(ReturnRequest.id == return_id).one(),
            order=db.query(Order).filter(Order.id == order_id).one(),
        )
        items = {
            int(row.order_item_id): row
            for row in db.query(ReturnLogisticsItem).filter(ReturnLogisticsItem.case_id == case.id).all()
        }
        authorize_item(
            db,
            case_id=case.id,
            item_id=items[order_item_ids[1]].id,
            quantity=1,
            idempotency_key=f"authorize-2-{token}",
            actor_admin_id=None,
            reason="line two",
        )
        db.commit()

        _expect_conflict(
            lambda: receive_item(
                db,
                case_id=case.id,
                item_id=items[order_item_ids[0]].id,
                quantity=1,
                idempotency_key=f"early-receive-{token}",
                actor_admin_id=None,
            ),
            "in transit",
        )
        db.rollback()

        mark_in_transit(db, case_id=case.id)
        db.commit()

        # An exact replay after lifecycle advancement is still idempotent: a
        # lost authorize response must never turn into a false operator error.
        replay_after_transit = authorize_item(
            db,
            case_id=case.id,
            item_id=items[order_item_ids[0]].id,
            quantity=2,
            idempotency_key=shared_key,
            actor_admin_id=None,
            reason="line one",
        )
        assert replay_after_transit.idempotent is True
        db.commit()

        _expect_conflict(
            lambda: authorize_item(
                db,
                case_id=case.id,
                item_id=items[order_item_ids[2]].id,
                quantity=1,
                idempotency_key=f"late-authorize-{token}",
                actor_admin_id=None,
            ),
            "frozen",
        )
        db.rollback()

        receive_item(
            db,
            case_id=case.id,
            item_id=items[order_item_ids[0]].id,
            quantity=2,
            idempotency_key=f"receive-1-{token}",
            actor_admin_id=None,
        )
        db.commit()

        _expect_conflict(
            lambda: inspect_item(
                db,
                case_id=case.id,
                item_id=items[order_item_ids[0]].id,
                quantity=1,
                disposition="resalable",
                idempotency_key=f"early-inspect-{token}",
                actor_admin_id=None,
            ),
            "fully received",
        )
        db.rollback()

        receive_item(
            db,
            case_id=case.id,
            item_id=items[order_item_ids[1]].id,
            quantity=1,
            idempotency_key=f"receive-2-{token}",
            actor_admin_id=None,
        )
        db.commit()

        stock_before = {
            variant_id: int(db.query(ProductVariant).filter(ProductVariant.id == variant_id).one().stock_qty)
            for variant_id in variant_ids
        }
        inspect_item(
            db,
            case_id=case.id,
            item_id=items[order_item_ids[0]].id,
            quantity=1,
            disposition="resalable",
            idempotency_key=f"inspect-resalable-{token}",
            actor_admin_id=None,
        )
        inspect_item(
            db,
            case_id=case.id,
            item_id=items[order_item_ids[0]].id,
            quantity=1,
            disposition="damaged",
            idempotency_key=f"inspect-damaged-{token}",
            actor_admin_id=None,
        )
        inspect_item(
            db,
            case_id=case.id,
            item_id=items[order_item_ids[1]].id,
            quantity=1,
            disposition="quarantine",
            idempotency_key=f"inspect-quarantine-{token}",
            actor_admin_id=None,
        )
        db.commit()

        db.expire_all()
        detail = _physical_payload(db, db.query(ReturnRequest).filter(ReturnRequest.id == return_id).one())
        assert detail["physical_status"] == "inspected"
        rows = {int(row["order_item_id"]): row for row in detail["items"]}
        assert rows[order_item_ids[0]]["authorized_qty"] == 2
        assert rows[order_item_ids[0]]["received_qty"] == 2
        assert rows[order_item_ids[0]]["inspected_qty"] == 2
        assert rows[order_item_ids[0]]["resalable_qty"] == 1
        assert rows[order_item_ids[0]]["damaged_qty"] == 1
        assert rows[order_item_ids[1]]["quarantine_qty"] == 1
        assert rows[order_item_ids[2]]["authorized_qty"] == 0

        assert db.query(ProductVariant).filter(ProductVariant.id == variant_ids[0]).one().stock_qty == stock_before[variant_ids[0]] + 1
        assert db.query(ProductVariant).filter(ProductVariant.id == variant_ids[1]).one().stock_qty == stock_before[variant_ids[1]]
        assert db.query(ProductVariant).filter(ProductVariant.id == variant_ids[2]).one().stock_qty == stock_before[variant_ids[2]]

        event_count = db.query(ReturnLogisticsEvent).filter(ReturnLogisticsEvent.case_id == case.id).count()
        assert event_count == 7, event_count

    print({
        "status": "ok",
        "preview_before_case": True,
        "case_scoped_idempotency": True,
        "reason_in_payload_fingerprint": True,
        "cross_case_sold_quantity_cap": True,
        "authorize_replay_after_transit": True,
        "authorization_frozen_after_transit": True,
        "receipt_requires_transit": True,
        "inspection_requires_full_receipt": True,
        "resalable_only_local_stock": True,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
