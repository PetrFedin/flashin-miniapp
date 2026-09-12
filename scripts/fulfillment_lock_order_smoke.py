#!/usr/bin/env python3
"""Prove fulfillment transitions lock Order before FulfillmentTask.

The guarded deadlock is:

* paid settlement / task creation: Order -> FulfillmentTask
* generic task transition (old): FulfillmentTask -> Order

One PostgreSQL transaction holds the Order row while a worker runs the real
fulfillment lock helper. The worker must block at Order, leaving the
FulfillmentTask row NOWAIT-lockable by the first transaction. Releasing that
transaction then allows the worker to acquire Order -> FulfillmentTask and
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
from backend.models import Customer, FulfillmentTask, Order
from backend.services import fulfillment_locking as locking


def main() -> int:
    token = uuid.uuid4().hex[:16]
    seed_db = SessionLocal()
    locker_db = SessionLocal()
    customer_id = None
    order_id = None
    task_id = None

    original_order_lock = locking._select_fulfillment_order_for_update
    original_task_lock = locking._select_fulfillment_task_for_update
    order_lock_attempted = threading.Event()
    task_lock_entered = threading.Event()
    worker_finished = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        customer = Customer(
            telegram_id=f"fulfillment-lock-{token}",
            username=f"fulfillment_lock_{token}",
            first_name="FulfillmentLock",
        )
        seed_db.add(customer)
        seed_db.flush()

        order = Order(
            customer_id=customer.id,
            status="paid",
            payment_status="paid",
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
            comment="Fulfillment lock-order CI smoke",
        )
        seed_db.add(order)
        seed_db.flush()

        task = FulfillmentTask(order_id=order.id, status="new")
        seed_db.add(task)
        seed_db.commit()

        customer_id = int(customer.id)
        order_id = int(order.id)
        task_id = int(task.id)

        def observed_order_lock(db, locked_order_id):
            order_lock_attempted.set()
            return original_order_lock(db, locked_order_id)

        def observed_task_lock(db, locked_task_id):
            task_lock_entered.set()
            return original_task_lock(db, locked_task_id)

        locking._select_fulfillment_order_for_update = observed_order_lock
        locking._select_fulfillment_task_for_update = observed_task_lock

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
                worker_order, worker_task = locking.lock_fulfillment_task_for_update(
                    worker_db,
                    task_id,
                )
                assert worker_order.id == order_id
                assert worker_task.id == task_id
                worker_db.commit()
            except BaseException as exc:  # surfaced in the main thread below
                worker_db.rollback()
                worker_errors.append(exc)
            finally:
                worker_finished.set()
                worker_db.close()

        worker = threading.Thread(
            target=run_transition_lock,
            name="fulfillment-order-lock-smoke",
        )
        worker.start()

        if not order_lock_attempted.wait(timeout=2):
            raise AssertionError("Fulfillment transition never attempted the Order row lock")
        if task_lock_entered.is_set():
            raise AssertionError("Fulfillment transition entered Task lock before acquiring Order")
        if worker_finished.is_set():
            raise AssertionError("Fulfillment transition did not block on the Order row lock")

        # Decisive regression check: with the old FulfillmentTask -> Order
        # sequence the worker would already own this task row and NOWAIT would
        # fail here. With Order-first locking this succeeds.
        locked_task = (
            locker_db.query(FulfillmentTask)
            .filter(FulfillmentTask.id == task_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_task.id == task_id
        assert not task_lock_entered.is_set()

        locker_db.commit()

        worker.join(timeout=6)
        if worker.is_alive():
            raise AssertionError("Fulfillment worker did not finish after Order lock release")
        if worker_errors:
            raise worker_errors[0]
        if not task_lock_entered.is_set():
            raise AssertionError("Fulfillment transition never reached the FulfillmentTask lock")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "task_id": task_id,
                    "lock_order": ["order", "fulfillment_task"],
                },
                indent=2,
            )
        )
        return 0
    finally:
        locking._select_fulfillment_order_for_update = original_order_lock
        locking._select_fulfillment_task_for_update = original_task_lock

        try:
            locker_db.rollback()
        finally:
            locker_db.close()
        seed_db.close()

        if task_id is not None:
            cleanup_db = SessionLocal()
            try:
                cleanup_db.query(FulfillmentTask).filter(FulfillmentTask.id == task_id).delete(
                    synchronize_session=False
                )
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
