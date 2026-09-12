#!/usr/bin/env python3
"""Prove settlement cannot take inventory before the checkout customer lock.

The deadlock being guarded against is:

* checkout:   Customer -> ProductVariant
* settlement (old): ProductVariant -> Customer

This smoke holds the customer row in one PostgreSQL transaction while a second
transaction runs the real ``settle_paid_order`` function. The settlement must
block at Customer before entering the real inventory commit. While it is
blocked, the first transaction must still be able to acquire the variant row
with NOWAIT. Releasing the first transaction then lets settlement finish.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal
from backend.models import (
    Customer,
    InventoryMovement,
    Order,
    OrderItem,
    Product,
    ProductVariant,
)
from backend.services import payment_settlement as settlement


def main() -> int:
    token = uuid.uuid4().hex[:16]
    seed_db = SessionLocal()
    locker_db = SessionLocal()
    customer_id = None
    product_id = None
    variant_id = None
    order_id = None

    original_lock_customer = settlement._lock_settlement_customer
    original_commit_inventory = settlement.commit_reservations_to_sold
    original_side_effects = {
        name: getattr(settlement, name)
        for name in (
            "queue_order_paid",
            "add_points",
            "mark_redemption_committed",
            "reward_referral_after_first_paid_order",
            "add_timeline_event",
            "ensure_fulfillment_task",
            "emit_event",
            "enqueue_webhook",
            "enqueue_event_for_destinations",
            "enqueue_moysklad_customer_order",
        )
    }

    customer_lock_attempted = threading.Event()
    inventory_entered = threading.Event()
    settlement_finished = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        customer = Customer(
            telegram_id=f"lock-smoke-{token}",
            username=f"lock_smoke_{token}",
            first_name="Lock",
        )
        product = Product(
            sku=f"LOCK-{token}",
            title="Settlement lock-order smoke",
            slug=f"settlement-lock-order-{token}",
            brand="FLASHIN",
            price=1000,
            currency="RUB",
            category="Testing",
            gender="unisex",
            active=True,
        )
        variant = ProductVariant(
            product=product,
            size="M",
            color="Black",
            sku=f"LOCK-V-{token}",
            stock_qty=2,
            reserved_qty=1,
        )
        seed_db.add_all([customer, product, variant])
        seed_db.flush()

        order = Order(
            customer_id=customer.id,
            status="created",
            payment_status="pending",
            delivery_status="not_started",
            total_amount=1000,
            delivery_price=0,
            discount_amount=0,
            loyalty_points_redeemed=0,
            loyalty_discount_amount=0,
            referral_code="",
            currency="RUB",
            delivery_type="pickup",
            address="",
            comment="Lock-order CI smoke",
        )
        seed_db.add(order)
        seed_db.flush()
        seed_db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                variant_id=variant.id,
                title=product.title,
                size=variant.size,
                quantity=1,
                price=1000,
            )
        )
        seed_db.commit()

        customer_id = int(customer.id)
        product_id = int(product.id)
        variant_id = int(variant.id)
        order_id = int(order.id)

        def observed_customer_lock(db, locked_customer_id):
            customer_lock_attempted.set()
            return original_lock_customer(db, locked_customer_id)

        def observed_inventory_commit(db, quantities, *, order_id, source):
            inventory_entered.set()
            return original_commit_inventory(
                db,
                quantities,
                order_id=order_id,
                source=source,
            )

        settlement._lock_settlement_customer = observed_customer_lock
        settlement.commit_reservations_to_sold = observed_inventory_commit
        for name in original_side_effects:
            setattr(settlement, name, lambda *_args, **_kwargs: None)

        locker_db.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked_customer = (
            locker_db.query(Customer)
            .filter(Customer.id == customer_id)
            .with_for_update()
            .one()
        )
        assert locked_customer.id == customer_id

        def run_settlement() -> None:
            worker_db = SessionLocal()
            try:
                worker_db.execute(text("SET LOCAL lock_timeout = '3s'"))
                worker_db.execute(text("SET LOCAL statement_timeout = '5s'"))
                worker_order = worker_db.query(Order).filter(Order.id == order_id).one()
                changed = settlement.settle_paid_order(worker_db, worker_order)
                if changed is not True:
                    raise AssertionError("Settlement unexpectedly reported no state change")
                worker_db.commit()
            except BaseException as exc:  # surfaced in the main thread below
                worker_db.rollback()
                worker_errors.append(exc)
            finally:
                settlement_finished.set()
                worker_db.close()

        worker = threading.Thread(target=run_settlement, name="settlement-lock-smoke")
        worker.start()

        if not customer_lock_attempted.wait(timeout=2):
            raise AssertionError("Settlement never attempted the customer row lock")
        if inventory_entered.is_set():
            raise AssertionError("Settlement entered inventory before acquiring the customer lock")
        if settlement_finished.is_set():
            raise AssertionError("Settlement did not block on the customer row lock")

        # This NOWAIT lock is the decisive regression check. With the old
        # Variant -> Customer order, the worker would already own this row and
        # PostgreSQL would reject this acquisition immediately.
        locked_variant = (
            locker_db.query(ProductVariant)
            .filter(ProductVariant.id == variant_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_variant.id == variant_id
        assert not inventory_entered.is_set()

        locker_db.commit()

        worker.join(timeout=6)
        if worker.is_alive():
            raise AssertionError("Settlement worker did not finish after lock release")
        if worker_errors:
            raise worker_errors[0]
        if not inventory_entered.is_set():
            raise AssertionError("Settlement never reached the inventory commit")

        verify_db = SessionLocal()
        try:
            persisted_order = verify_db.query(Order).filter(Order.id == order_id).one()
            persisted_variant = (
                verify_db.query(ProductVariant).filter(ProductVariant.id == variant_id).one()
            )
            assert persisted_order.status == "paid"
            assert persisted_order.payment_status == "paid"
            assert persisted_variant.stock_qty == 1
            assert persisted_variant.reserved_qty == 0
        finally:
            verify_db.close()

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "customer_id": customer_id,
                    "variant_id": variant_id,
                    "lock_order": ["customer", "variant"],
                },
                indent=2,
            )
        )
        return 0
    finally:
        settlement._lock_settlement_customer = original_lock_customer
        settlement.commit_reservations_to_sold = original_commit_inventory
        for name, original in original_side_effects.items():
            setattr(settlement, name, original)

        try:
            locker_db.rollback()
        finally:
            locker_db.close()
        seed_db.close()

        if order_id is not None:
            cleanup_db = SessionLocal()
            try:
                cleanup_db.query(InventoryMovement).filter(
                    InventoryMovement.order_id == order_id
                ).delete(synchronize_session=False)
                cleanup_db.query(OrderItem).filter(OrderItem.order_id == order_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(Order).filter(Order.id == order_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(ProductVariant).filter(
                    ProductVariant.id == variant_id
                ).delete(synchronize_session=False)
                cleanup_db.query(Product).filter(Product.id == product_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(Customer).filter(Customer.id == customer_id).delete(
                    synchronize_session=False
                )
                cleanup_db.commit()
            except BaseException:
                cleanup_db.rollback()
                raise
            finally:
                cleanup_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
