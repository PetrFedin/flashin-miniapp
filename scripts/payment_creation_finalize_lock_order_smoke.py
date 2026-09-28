#!/usr/bin/env python3
"""Prove payment finalization cannot lock Attempt before Order.

The deadlock being guarded against is:

* begin/retry: Order -> Payment -> PaymentCreationAttempt
* finalize(old): PaymentCreationAttempt -> Order

One PostgreSQL transaction holds the Order row while a worker runs the real
finalization lock helper. The worker must block at Order, leaving the Attempt
row NOWAIT-lockable by the first transaction. Releasing that transaction then
allows the worker to acquire Order -> Attempt and finish.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, utcnow_naive
from backend.models import Customer, Order
from backend.payment_attempt_models import PaymentCreationAttempt
from backend.payment_attempt_statuses import CREATING_PAYMENT_ATTEMPT_STATUS
from backend.services import payment_creation as creation


def main() -> int:
    token = uuid.uuid4().hex[:16]
    seed_db = SessionLocal()
    locker_db = SessionLocal()
    customer_id = None
    order_id = None
    attempt_id = None

    original_order_lock = creation._lock_payment_creation_order
    original_attempt_lock = creation._lock_payment_creation_attempt
    order_lock_attempted = threading.Event()
    attempt_lock_entered = threading.Event()
    worker_finished = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        customer = Customer(
            telegram_id=f"payment-lock-{token}",
            username=f"payment_lock_{token}",
            first_name="PaymentLock",
        )
        seed_db.add(customer)
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
            comment="Payment creation lock-order CI smoke",
        )
        seed_db.add(order)
        seed_db.flush()

        attempt = PaymentCreationAttempt(
            order_id=order.id,
            provider="yookassa",
            attempt_number=1,
            status=CREATING_PAYMENT_ATTEMPT_STATUS,
            lease_expires_at=utcnow_naive() + timedelta(minutes=5),
            last_error="",
            updated_at=utcnow_naive(),
        )
        seed_db.add(attempt)
        seed_db.commit()

        customer_id = int(customer.id)
        order_id = int(order.id)
        attempt_id = int(attempt.id)

        def observed_order_lock(db, locked_order_id):
            order_lock_attempted.set()
            return original_order_lock(db, locked_order_id)

        def observed_attempt_lock(db, locked_attempt_id):
            attempt_lock_entered.set()
            return original_attempt_lock(db, locked_attempt_id)

        creation._lock_payment_creation_order = observed_order_lock
        creation._lock_payment_creation_attempt = observed_attempt_lock

        locker_db.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked_order = (
            locker_db.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .one()
        )
        assert locked_order.id == order_id

        def run_finalize_lock() -> None:
            worker_db = SessionLocal()
            try:
                worker_db.execute(text("SET LOCAL lock_timeout = '3s'"))
                worker_db.execute(text("SET LOCAL statement_timeout = '5s'"))
                worker_order, worker_attempt = creation._lock_finalize_order_then_attempt(
                    worker_db,
                    attempt_id,
                )
                assert worker_order.id == order_id
                assert worker_attempt.id == attempt_id
                worker_db.commit()
            except BaseException as exc:  # surfaced in the main thread below
                worker_db.rollback()
                worker_errors.append(exc)
            finally:
                worker_finished.set()
                worker_db.close()

        worker = threading.Thread(target=run_finalize_lock, name="payment-finalize-lock-smoke")
        worker.start()

        if not order_lock_attempted.wait(timeout=2):
            raise AssertionError("Payment finalization never attempted the Order row lock")
        if attempt_lock_entered.is_set():
            raise AssertionError("Payment finalization entered Attempt lock before acquiring Order")
        if worker_finished.is_set():
            raise AssertionError("Payment finalization did not block on the Order row lock")

        # Decisive regression check: with the old Attempt -> Order order the
        # worker would already own this Attempt row and NOWAIT would fail.
        locked_attempt = (
            locker_db.query(PaymentCreationAttempt)
            .filter(PaymentCreationAttempt.id == attempt_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_attempt.id == attempt_id
        assert not attempt_lock_entered.is_set()

        locker_db.commit()

        worker.join(timeout=6)
        if worker.is_alive():
            raise AssertionError("Payment finalization worker did not finish after Order lock release")
        if worker_errors:
            raise worker_errors[0]
        if not attempt_lock_entered.is_set():
            raise AssertionError("Payment finalization never reached the Attempt lock")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "attempt_id": attempt_id,
                    "lock_order": ["order", "payment_creation_attempt"],
                },
                indent=2,
            )
        )
        return 0
    finally:
        creation._lock_payment_creation_order = original_order_lock
        creation._lock_payment_creation_attempt = original_attempt_lock

        try:
            locker_db.rollback()
        finally:
            locker_db.close()
        seed_db.close()

        if attempt_id is not None:
            cleanup_db = SessionLocal()
            try:
                cleanup_db.query(PaymentCreationAttempt).filter(
                    PaymentCreationAttempt.id == attempt_id
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
