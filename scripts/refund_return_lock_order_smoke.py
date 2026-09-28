#!/usr/bin/env python3
"""Prove refund approval cannot lock ReturnRequest before Order.

The guarded deadlock is:

* create_return: Order -> ReturnRequest
* approve/recovery/webhook(old): ReturnRequest -> Order

One PostgreSQL transaction holds the Order row while a worker runs the real
approval lock helper. The worker must block at Order, leaving the ReturnRequest
row NOWAIT-lockable by the first transaction. Releasing that transaction then
allows the worker to acquire Order -> ReturnRequest and finish.
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
from backend.models import Customer, Order, ReturnRequest
from backend.services import refund_locking as locking


def main() -> int:
    token = uuid.uuid4().hex[:16]
    seed_db = SessionLocal()
    locker_db = SessionLocal()
    customer_id = None
    order_id = None
    return_id = None

    original_order_lock = locking._select_refund_order_for_update
    original_return_lock = locking._select_return_request_for_update
    order_lock_attempted = threading.Event()
    return_lock_entered = threading.Event()
    worker_finished = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        customer = Customer(
            telegram_id=f"refund-lock-{token}",
            username=f"refund_lock_{token}",
            first_name="RefundLock",
        )
        seed_db.add(customer)
        seed_db.flush()

        order = Order(
            customer_id=customer.id,
            status="refund_requested",
            payment_status="paid",
            delivery_status="delivered",
            total_amount=1000,
            delivery_price=0,
            discount_amount=0,
            loyalty_points_redeemed=0,
            loyalty_discount_amount=0,
            referral_code="",
            currency="RUB",
            delivery_type="pickup",
            address="",
            comment="Refund return lock-order CI smoke",
        )
        seed_db.add(order)
        seed_db.flush()

        ret = ReturnRequest(
            order_id=order.id,
            customer_id=customer.id,
            reason="Refund lock-order smoke request",
            status="requested",
            provider_refund_id="",
            refund_amount=0,
        )
        seed_db.add(ret)
        seed_db.commit()

        customer_id = int(customer.id)
        order_id = int(order.id)
        return_id = int(ret.id)

        def observed_order_lock(db, locked_order_id):
            order_lock_attempted.set()
            return original_order_lock(db, locked_order_id)

        def observed_return_lock(db, locked_return_id):
            return_lock_entered.set()
            return original_return_lock(db, locked_return_id)

        locking._select_refund_order_for_update = observed_order_lock
        locking._select_return_request_for_update = observed_return_lock

        locker_db.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked_order = (
            locker_db.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .one()
        )
        assert locked_order.id == order_id

        def run_approval_lock() -> None:
            worker_db = SessionLocal()
            try:
                worker_db.execute(text("SET LOCAL lock_timeout = '3s'"))
                worker_db.execute(text("SET LOCAL statement_timeout = '5s'"))
                worker_order, worker_return = locking.lock_return_request_for_approval(
                    worker_db,
                    return_id,
                )
                assert worker_order.id == order_id
                assert worker_return.id == return_id
                worker_db.commit()
            except BaseException as exc:  # surfaced in the main thread below
                worker_db.rollback()
                worker_errors.append(exc)
            finally:
                worker_finished.set()
                worker_db.close()

        worker = threading.Thread(target=run_approval_lock, name="refund-return-lock-smoke")
        worker.start()

        if not order_lock_attempted.wait(timeout=2):
            raise AssertionError("Refund approval never attempted the Order row lock")
        if return_lock_entered.is_set():
            raise AssertionError("Refund approval entered ReturnRequest lock before acquiring Order")
        if worker_finished.is_set():
            raise AssertionError("Refund approval did not block on the Order row lock")

        # Decisive regression check: with the old ReturnRequest -> Order order
        # the worker would already own this row and NOWAIT would fail here.
        locked_return = (
            locker_db.query(ReturnRequest)
            .filter(ReturnRequest.id == return_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_return.id == return_id
        assert not return_lock_entered.is_set()

        locker_db.commit()

        worker.join(timeout=6)
        if worker.is_alive():
            raise AssertionError("Refund approval worker did not finish after Order lock release")
        if worker_errors:
            raise worker_errors[0]
        if not return_lock_entered.is_set():
            raise AssertionError("Refund approval never reached the ReturnRequest lock")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "return_id": return_id,
                    "lock_order": ["order", "return_request"],
                },
                indent=2,
            )
        )
        return 0
    finally:
        locking._select_refund_order_for_update = original_order_lock
        locking._select_return_request_for_update = original_return_lock

        try:
            locker_db.rollback()
        finally:
            locker_db.close()
        seed_db.close()

        if return_id is not None:
            cleanup_db = SessionLocal()
            try:
                cleanup_db.query(ReturnRequest).filter(ReturnRequest.id == return_id).delete(
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
