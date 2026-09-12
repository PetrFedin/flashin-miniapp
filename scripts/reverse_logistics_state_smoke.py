#!/usr/bin/env python3
"""Prove financial refunds cannot create phantom sellable inventory."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine
from backend.models import Customer, InventoryMovement, Order, OrderItem, Product, ProductVariant, ReturnRequest
from backend.provider_models import ProviderCommand
from backend.reverse_logistics_models import ReturnLogisticsItem
from backend.services.refund_state import apply_provider_refund_status
from backend.services.reverse_logistics import (
    authorize_item,
    ensure_physical_case,
    inspect_item,
    mark_in_transit,
    receive_item,
)


def _fixture(db, token: str, suffix: str, quantity: int = 3):
    customer = Customer(telegram_id=f"reverse-{token}-{suffix}", first_name="Reverse")
    product = Product(
        sku=f"REV-{token}-{suffix}",
        slug=f"reverse-{token}-{suffix}",
        title="Reverse Logistics",
        price=100.0,
        currency="RUB",
        active=True,
    )
    variant = ProductVariant(
        product=product,
        size="M",
        color="Black",
        sku=f"REV-V-{token}-{suffix}",
        stock_qty=7,
        reserved_qty=0,
    )
    db.add_all([customer, product, variant])
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="completed",
        payment_status="paid",
        delivery_status="delivered",
        total_amount=float(quantity * 100),
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
        size="M",
        quantity=quantity,
        price=100.0,
    )
    ret = ReturnRequest(
        order_id=order.id,
        customer_id=customer.id,
        reason="Reverse logistics smoke",
        status="refund_pending",
        provider_refund_id=f"refund-{token}-{suffix}",
        refund_amount=float(quantity * 100),
    )
    db.add_all([order_item, ret])
    db.commit()
    return customer.id, order.id, order_item.id, variant.id, ret.id


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("reverse logistics smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:12]
    with SessionLocal() as db:
        _, order_id, order_item_id, variant_id, return_id = _fixture(db, token, "physical")
        order = db.query(Order).filter(Order.id == order_id).one()
        ret = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).one()
        stock_before_refund = db.query(ProductVariant).filter(ProductVariant.id == variant_id).one().stock_qty

        financial = apply_provider_refund_status(db, ret, order, "succeeded")
        db.commit()
        db.expire_all()
        assert db.query(ProductVariant).filter(ProductVariant.id == variant_id).one().stock_qty == stock_before_refund
        assert financial["inventory_effect"] == "none_financial_refund_is_not_physical_return"
        assert (
            db.query(InventoryMovement)
            .filter(InventoryMovement.order_id == order_id, InventoryMovement.kind == "return")
            .count()
            == 0
        )
        assert (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == "moysklad",
                ProviderCommand.command_type == "moysklad.sales_return.create",
            )
            .count()
            == 0
        )

        order = db.query(Order).filter(Order.id == order_id).one()
        ret = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).one()
        case = ensure_physical_case(db, ret=ret, order=order)
        item = (
            db.query(ReturnLogisticsItem)
            .filter(ReturnLogisticsItem.case_id == case.id, ReturnLogisticsItem.order_item_id == order_item_id)
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
        mark_in_transit(db, case_id=case.id)
        receive_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=2,
            idempotency_key=f"receive-{token}",
            actor_admin_id=None,
        )
        damaged = inspect_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=1,
            disposition="damaged",
            idempotency_key=f"inspect-damaged-{token}",
            actor_admin_id=None,
        )
        assert damaged.idempotent is False
        assert db.query(ProductVariant).filter(ProductVariant.id == variant_id).one().stock_qty == stock_before_refund

        resalable = inspect_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=1,
            disposition="resalable",
            idempotency_key=f"inspect-resalable-{token}",
            actor_admin_id=None,
        )
        assert resalable.idempotent is False
        assert db.query(ProductVariant).filter(ProductVariant.id == variant_id).one().stock_qty == stock_before_refund + 1
        db.commit()

        duplicate = inspect_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=1,
            disposition="resalable",
            idempotency_key=f"inspect-resalable-{token}",
            actor_admin_id=None,
        )
        assert duplicate.idempotent is True
        db.commit()
        db.expire_all()
        assert db.query(ProductVariant).filter(ProductVariant.id == variant_id).one().stock_qty == stock_before_refund + 1
        movements = (
            db.query(InventoryMovement)
            .filter(
                InventoryMovement.order_id == order_id,
                InventoryMovement.variant_id == variant_id,
                InventoryMovement.kind == "return",
            )
            .all()
        )
        assert len(movements) == 1
        assert movements[0].quantity == 1
        assert str(movements[0].source).startswith("reverse_logistics_event:")

        _, goodwill_order_id, _, goodwill_variant_id, goodwill_return_id = _fixture(db, token, "goodwill", quantity=1)
        goodwill_order = db.query(Order).filter(Order.id == goodwill_order_id).one()
        goodwill_ret = db.query(ReturnRequest).filter(ReturnRequest.id == goodwill_return_id).one()
        goodwill_stock = db.query(ProductVariant).filter(ProductVariant.id == goodwill_variant_id).one().stock_qty
        apply_provider_refund_status(db, goodwill_ret, goodwill_order, "succeeded")
        db.commit()
        db.expire_all()
        assert db.query(ProductVariant).filter(ProductVariant.id == goodwill_variant_id).one().stock_qty == goodwill_stock

        print({
            "status": "ok",
            "financial_refund_stock_delta": 0,
            "damaged_stock_delta": 0,
            "resalable_stock_delta": 1,
            "partial_physical_return": "2_of_3",
            "duplicate_inspection_stock_delta": 0,
            "goodwill_no_return_stock_delta": 0,
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
