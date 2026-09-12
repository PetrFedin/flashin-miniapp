#!/usr/bin/env python3
"""Prove delivery transitions lock Order before DeliveryShipment.

The guarded deadlock is:

* shipment create/idempotent ensure: Order -> DeliveryShipment
* generic shipment transition (old): DeliveryShipment -> Order

One PostgreSQL transaction holds the Order row while a worker runs the real
delivery lock helper. The worker must block at Order, leaving the
DeliveryShipment row NOWAIT-lockable by the first transaction. Releasing that
transaction then allows the worker to acquire Order -> DeliveryShipment and
finish.
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
from backend.models import Customer, DeliveryShipment, Order
from backend.services import delivery_locking as locking


def main() -> int:
    token = uuid.uuid4().hex[:16]
    seed_db = SessionLocal()
    locker_db = SessionLocal()
    customer_id = None
    order_id = None
    shipment_id = None

    original_order_lock = locking._select_delivery_order_for_update
    original_shipment_lock = locking._select_delivery_shipment_for_update
    order_lock_attempted = threading.Event()
    shipment_lock_entered = threading.Event()
    worker_finished = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        customer = Customer(
            telegram_id=f"delivery-lock-{token}",
            username=f"delivery_lock_{token}",
            first_name="DeliveryLock",
        )
        seed_db.add(customer)
        seed_db.flush()

        order = Order(
            customer_id=customer.id,
            status="ready",
            payment_status="paid",
            delivery_status="ready",
            total_amount=1000,
            delivery_price=0,
            discount_amount=0,
            loyalty_points_redeemed=0,
            loyalty_discount_amount=0,
            referral_code="",
            currency="RUB",
            delivery_type="courier",
            address="Delivery lock-order CI smoke",
            comment="Delivery lock-order CI smoke",
        )
        seed_db.add(order)
        seed_db.flush()

        shipment = DeliveryShipment(
            order_id=order.id,
            provider_code="courier",
            tracking_number="",
            status="created",
            price=0,
            raw_payload="{}",
        )
        seed_db.add(shipment)
        seed_db.commit()

        customer_id = int(customer.id)
        order_id = int(order.id)
        shipment_id = int(shipment.id)

        def observed_order_lock(db, locked_order_id):
            order_lock_attempted.set()
            return original_order_lock(db, locked_order_id)

        def observed_shipment_lock(db, locked_shipment_id):
            shipment_lock_entered.set()
            return original_shipment_lock(db, locked_shipment_id)

        locking._select_delivery_order_for_update = observed_order_lock
        locking._select_delivery_shipment_for_update = observed_shipment_lock

        locker_db.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked_order = (
            locker_db.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .one()
        )
        assert locked_order.id == order_id

        def run_transition_lock() -> None:
            worker_db = SessionLocal()
            try:
                worker_db.execute(text("SET LOCAL lock_timeout = '3s'"))
                worker_db.execute(text("SET LOCAL statement_timeout = '5s'"))
                worker_order, worker_shipment = locking.lock_delivery_shipment_for_update(
                    worker_db,
                    shipment_id,
                )
                assert worker_order.id == order_id
                assert worker_shipment.id == shipment_id
                worker_db.commit()
            except BaseException as exc:  # surfaced in the main thread below
                worker_db.rollback()
                worker_errors.append(exc)
            finally:
                worker_finished.set()
                worker_db.close()

        worker = threading.Thread(
            target=run_transition_lock,
            name="delivery-order-lock-smoke",
        )
        worker.start()

        if not order_lock_attempted.wait(timeout=2):
            raise AssertionError("Delivery transition never attempted the Order row lock")
        if shipment_lock_entered.is_set():
            raise AssertionError("Delivery transition entered Shipment lock before acquiring Order")
        if worker_finished.is_set():
            raise AssertionError("Delivery transition did not block on the Order row lock")

        # Decisive regression check: with the old DeliveryShipment -> Order
        # sequence the worker would already own this shipment row and NOWAIT
        # would fail here. With Order-first locking this succeeds.
        locked_shipment = (
            locker_db.query(DeliveryShipment)
            .filter(DeliveryShipment.id == shipment_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_shipment.id == shipment_id
        assert not shipment_lock_entered.is_set()

        locker_db.commit()

        worker.join(timeout=6)
        if worker.is_alive():
            raise AssertionError("Delivery worker did not finish after Order lock release")
        if worker_errors:
            raise worker_errors[0]
        if not shipment_lock_entered.is_set():
            raise AssertionError("Delivery transition never reached the DeliveryShipment lock")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "shipment_id": shipment_id,
                    "lock_order": ["order", "delivery_shipment"],
                },
                indent=2,
            )
        )
        return 0
    finally:
        locking._select_delivery_order_for_update = original_order_lock
        locking._select_delivery_shipment_for_update = original_shipment_lock

        try:
            locker_db.rollback()
        finally:
            locker_db.close()
        seed_db.close()

        if shipment_id is not None:
            cleanup_db = SessionLocal()
            try:
                cleanup_db.query(DeliveryShipment).filter(
                    DeliveryShipment.id == shipment_id
                ).delete(synchronize_session=False)
                cleanup_db.query(Order).filter(Order.id == order_id).delete(
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
