#!/usr/bin/env python3
"""Prove refund item allocation authority on real PostgreSQL."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import engine, utcnow_naive
from backend.models import Customer, Order, OrderItem, Product, ProductVariant, ReturnRequest
from backend.refund_allocation_models import ReturnRefundAllocation
from backend.reverse_logistics_models import (
    ReturnLogisticsCase,
    ReturnLogisticsEvent,
    ReturnLogisticsItem,
)
from backend.services.refund_allocation import (
    RefundAllocationError,
    ensure_refund_allocation,
    reconcile_refund_allocations,
    refund_allocation_options,
)
from backend.services.order_money_allocation import allocate_order_money


class _Spec:
    def __init__(
        self,
        component_kind: str,
        amount: float,
        *,
        order_item_id: int | None = None,
        quantity_evidence: int | None = None,
    ):
        self.component_kind = component_kind
        self.amount = amount
        self.order_item_id = order_item_id
        self.quantity_evidence = quantity_evidence


def _return(db: Session, order: Order, token: str, suffix: str) -> ReturnRequest:
    row = ReturnRequest(
        order_id=order.id,
        customer_id=order.customer_id,
        reason=f"refund allocation smoke {suffix} {token}",
        status="requested",
        refund_amount=0,
    )
    db.add(row)
    db.flush()
    return row


def _catalog(db: Session, token: str, suffix: str, price: float) -> tuple[Product, ProductVariant]:
    product = Product(
        sku=f"ALLOC-{suffix}-{token}",
        title=f"Allocation {suffix}",
        slug=f"allocation-{suffix.lower()}-{token}",
        brand="FLASHIN",
        price=price,
        currency="RUB",
        category="Testing",
        gender="unisex",
        active=True,
    )
    variant = ProductVariant(
        product=product,
        size="M",
        color="Black",
        sku=f"ALLOC-{suffix}-V-{token}",
        stock_qty=50,
        reserved_qty=0,
    )
    db.add_all([product, variant])
    db.flush()
    return product, variant


def _item(
    db: Session,
    order: Order,
    product: Product,
    variant: ProductVariant,
    *,
    quantity: int,
    price: float,
) -> OrderItem:
    row = OrderItem(
        order_id=order.id,
        product_id=product.id,
        variant_id=variant.id,
        title=product.title,
        size="M",
        quantity=quantity,
        price=price,
    )
    db.add(row)
    db.flush()
    return row


def _physical_case(
    db: Session,
    *,
    ret: ReturnRequest,
    item: OrderItem,
    token: str,
    suffix: str,
) -> ReturnLogisticsCase:
    case = ReturnLogisticsCase(
        return_request_id=ret.id,
        order_id=ret.order_id,
        customer_id=ret.customer_id,
        status="inspected",
        created_at=utcnow_naive(),
        updated_at=utcnow_naive(),
    )
    db.add(case)
    db.flush()
    physical = ReturnLogisticsItem(
        case_id=case.id,
        order_item_id=item.id,
        variant_id=item.variant_id,
        ordered_qty=item.quantity,
        authorized_qty=1,
        received_qty=1,
        inspected_qty=1,
        resalable_qty=1,
        damaged_qty=0,
        quarantine_qty=0,
    )
    db.add(physical)
    db.flush()
    db.add(
        ReturnLogisticsEvent(
            case_id=case.id,
            item_id=physical.id,
            event_type="inspected",
            disposition="resalable",
            quantity=1,
            idempotency_key=f"alloc-smoke-{token}-{suffix}",
            payload_hash=(suffix.encode("utf-8").hex() * 64)[:64].ljust(64, "0"),
            actor_admin_id=None,
            reason="refund allocation postgres smoke",
            created_at=utcnow_naive(),
        )
    )
    db.flush()
    return case


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("refund item allocation smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:16]
    connection = engine.connect()
    outer = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        customer = Customer(
            telegram_id=str(int(token, 16)),
            username=f"refund_alloc_{token}",
            first_name="Refund Allocation",
        )
        db.add(customer)
        db.flush()

        # Scenario A: promo + loyalty + delivery + multi-line staged partials + goodwill.
        p1, v1 = _catalog(db, token, "A", 1000.0)
        p2, v2 = _catalog(db, token, "B", 333.33)
        order = Order(
            customer_id=customer.id,
            status="paid",
            payment_status="paid",
            total_amount=1850.0,
            delivery_price=150.0,
            discount_amount=199.99,
            loyalty_discount_amount=100.0,
            loyalty_points_redeemed=100.0,
            currency="RUB",
            delivery_type="courier",
        )
        db.add(order)
        db.flush()
        item_a = _item(db, order, p1, v1, quantity=1, price=1000.0)
        item_b = _item(db, order, p2, v2, quantity=3, price=333.33)

        policy = allocate_order_money(order, [item_a, item_b])
        assert [line.net_cents for line in policy.lines] == [85000, 85000]
        assert policy.delivery_cents == 15000
        assert policy.order_total_cents == 185000

        first = _return(db, order, token, "first")
        first_evidence = ensure_refund_allocation(
            db,
            order=order,
            ret=first,
            requested_amount=700,
            raw_allocations=[
                _Spec("item", 500, order_item_id=item_a.id),
                _Spec("delivery", 100),
                _Spec("goodwill", 100),
            ],
        )
        first.refund_amount = 700
        first.status = "approved_partial"
        db.commit()
        assert first_evidence["allocated_cents"] == 70000

        second = _return(db, order, token, "second")
        second_evidence = ensure_refund_allocation(
            db,
            order=order,
            ret=second,
            requested_amount=700,
            raw_allocations=[
                _Spec("item", 350, order_item_id=item_a.id),
                _Spec("item", 300, order_item_id=item_b.id),
                _Spec("delivery", 50),
            ],
        )
        second.refund_amount = 700
        second.status = "approved_partial"
        db.commit()
        assert second_evidence["allocated_cents"] == 70000

        over = _return(db, order, token, "over")
        try:
            ensure_refund_allocation(
                db,
                order=order,
                ret=over,
                requested_amount=1,
                raw_allocations=[_Spec("item", 1, order_item_id=item_a.id)],
            )
        except RefundAllocationError as exc:
            assert "remaining value" in str(exc)
            db.rollback()
        else:
            raise AssertionError("item over-allocation unexpectedly succeeded")

        # Reacquire after rollback because the failed return was only test scaffolding.
        order = db.query(Order).filter(Order.id == order.id).one()
        item_b = db.query(OrderItem).filter(OrderItem.id == item_b.id).one()
        final = _return(db, order, token, "final")
        final_evidence = ensure_refund_allocation(
            db,
            order=order,
            ret=final,
            requested_amount=450,
            raw_allocations=[
                _Spec("item", 450, order_item_id=item_b.id),
            ],
        )
        final.refund_amount = 450
        final.status = "approved"
        db.commit()
        assert final_evidence["allocated_cents"] == 45000

        rows = (
            db.query(ReturnRefundAllocation)
            .filter(ReturnRefundAllocation.order_id == order.id)
            .all()
        )
        assert sum(int(row.amount_cents) for row in rows) == 185000
        assert sum(
            int(row.amount_cents)
            for row in rows
            if row.component_kind == "goodwill"
        ) == 10000
        options = refund_allocation_options(db, order=order)
        assert options["allocated_cents"] == 185000

        # Scenario B: exact three-way quantity rounding, then matching physical value.
        rp, rv = _catalog(db, token, "ROUND", 333.33)
        rounding_order = Order(
            customer_id=customer.id,
            status="paid",
            payment_status="paid",
            total_amount=850.0,
            delivery_price=0,
            discount_amount=149.99,
            loyalty_discount_amount=0,
            currency="RUB",
            delivery_type="pickup",
        )
        db.add(rounding_order)
        db.flush()
        rounding_item = _item(
            db,
            rounding_order,
            rp,
            rv,
            quantity=3,
            price=333.33,
        )
        expected = [28333, 28334, 28333]
        rounding_returns: list[ReturnRequest] = []
        for index, cents in enumerate(expected, start=1):
            ret = _return(db, rounding_order, token, f"round-{index}")
            evidence = ensure_refund_allocation(
                db,
                order=rounding_order,
                ret=ret,
                requested_amount=cents / 100,
                raw_allocations=[
                    _Spec(
                        "item",
                        cents / 100,
                        order_item_id=rounding_item.id,
                        quantity_evidence=1,
                    )
                ],
            )
            assert evidence["item_cents"] == cents
            ret.refund_amount = cents / 100
            ret.status = "approved" if index == 3 else "approved_partial"
            db.flush()
            rounding_returns.append(ret)
            _physical_case(
                db,
                ret=ret,
                item=rounding_item,
                token=token,
                suffix=f"round-{index}",
            )
            db.commit()

        rounded_rows = (
            db.query(ReturnRefundAllocation)
            .filter(ReturnRefundAllocation.order_id == rounding_order.id)
            .order_by(ReturnRefundAllocation.id.asc())
            .all()
        )
        assert [int(row.amount_cents) for row in rounded_rows] == expected
        assert sum(int(row.amount_cents) for row in rounded_rows) == 85000

        rounded_reconciliation = reconcile_refund_allocations(
            db,
            rounding_order.id,
        )
        assert rounded_reconciliation["status"] == "PASS"
        assert rounded_reconciliation["completed_item_cents"] == 85000
        assert rounded_reconciliation["physical_item_cents"] == 85000
        assert rounded_reconciliation["lines"] == [
            {
                "order_item_id": rounding_item.id,
                "financial_cents": 85000,
                "physical_cents": 85000,
                "delta_cents": 0,
            }
        ]

        # Scenario C: goodwill never fabricates a physical item requirement.
        gp, gv = _catalog(db, token, "GOODWILL", 1000.0)
        goodwill_order = Order(
            customer_id=customer.id,
            status="paid",
            payment_status="paid",
            total_amount=1000.0,
            delivery_price=0,
            discount_amount=0,
            loyalty_discount_amount=0,
            currency="RUB",
            delivery_type="pickup",
        )
        db.add(goodwill_order)
        db.flush()
        _item(db, goodwill_order, gp, gv, quantity=1, price=1000.0)
        goodwill = _return(db, goodwill_order, token, "goodwill")
        ensure_refund_allocation(
            db,
            order=goodwill_order,
            ret=goodwill,
            requested_amount=100,
            raw_allocations=[_Spec("goodwill", 100)],
        )
        goodwill.refund_amount = 100
        goodwill.status = "approved_partial"
        db.commit()

        goodwill_reconciliation = reconcile_refund_allocations(
            db,
            goodwill_order.id,
        )
        assert goodwill_reconciliation["status"] == "PASS"
        assert goodwill_reconciliation["completed_goodwill_cents"] == 10000
        assert goodwill_reconciliation["physical_item_cents"] == 0

        print(
            json.dumps(
                {
                    "status": "ok",
                    "policy_version": 1,
                    "discount_loyalty_delivery_reconciled": True,
                    "multi_line_staged_refunds": True,
                    "goodwill_explicit": True,
                    "over_allocation_blocked": True,
                    "rounding_cents": expected,
                    "quantity_backed_rounding_exact": True,
                    "financial_physical_status": "PASS",
                    "inventory_mutations_from_financial_allocation": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        db.close()
        if outer.is_active:
            outer.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
