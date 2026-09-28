#!/usr/bin/env python3
"""Prove cart loyalty locks CrmProfile before LoyaltyRedemptionHold.

The guarded deadlock is:

* checkout: CrmProfile -> LoyaltyRedemptionHold
* cart loyalty (old): LoyaltyRedemptionHold -> CrmProfile

One PostgreSQL transaction holds the customer's CrmProfile while a worker runs
real cart loyalty reconciliation. The worker must block at CrmProfile, leaving
the exact reserved hold NOWAIT-lockable by the first transaction. Releasing the
profile transaction then allows the worker to acquire
CrmProfile -> LoyaltyRedemptionHold and finish.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal
from backend.models import Cart, CrmProfile, Customer, LoyaltyRedemptionHold
from backend.services import cart_adjustments


def main() -> int:
    token = uuid.uuid4().hex[:16]
    seed_db = SessionLocal()
    locker_db = SessionLocal()
    customer_id = None
    cart_id = None
    profile_id = None
    hold_id = None

    original_profile_lock = cart_adjustments._locked_loyalty_profile
    original_holds_lock = cart_adjustments._reserved_holds
    profile_lock_attempted = threading.Event()
    holds_lock_entered = threading.Event()
    worker_finished = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        customer = Customer(
            telegram_id=f"loyalty-lock-{token}",
            username=f"loyalty_lock_{token}",
            first_name="LoyaltyLock",
        )
        seed_db.add(customer)
        seed_db.flush()

        cart = Cart(
            customer_id=customer.id,
            status="active",
            loyalty_points_to_redeem=Decimal("100.0000"),
        )
        seed_db.add(cart)
        seed_db.flush()

        profile = CrmProfile(
            customer_id=customer.id,
            segment="new",
            loyalty_points=Decimal("500.0000"),
        )
        seed_db.add(profile)
        seed_db.flush()

        hold = LoyaltyRedemptionHold(
            customer_id=customer.id,
            cart_id=cart.id,
            points=Decimal("100.0000"),
            status="reserved",
        )
        seed_db.add(hold)
        seed_db.commit()

        customer_id = int(customer.id)
        cart_id = int(cart.id)
        profile_id = int(profile.id)
        hold_id = int(hold.id)

        def observed_profile_lock(db, locked_customer_id):
            profile_lock_attempted.set()
            return original_profile_lock(db, locked_customer_id)

        def observed_holds_lock(db, locked_cart):
            holds_lock_entered.set()
            return original_holds_lock(db, locked_cart)

        cart_adjustments._locked_loyalty_profile = observed_profile_lock
        cart_adjustments._reserved_holds = observed_holds_lock

        locker_db.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked_profile = (
            locker_db.query(CrmProfile)
            .filter(CrmProfile.id == profile_id)
            .with_for_update()
            .one()
        )
        assert locked_profile.customer_id == customer_id

        def run_reconciliation() -> None:
            worker_db = SessionLocal()
            try:
                worker_db.execute(text("SET LOCAL lock_timeout = '3s'"))
                worker_db.execute(text("SET LOCAL statement_timeout = '5s'"))
                worker_cart = worker_db.query(Cart).filter(Cart.id == cart_id).one()
                loyalty_points, loyalty_discount = cart_adjustments._reconcile_loyalty(
                    worker_db,
                    worker_cart,
                    Decimal("1000.00"),
                    Decimal("0.00"),
                )
                assert loyalty_points == 100
                assert loyalty_discount == Decimal("100.00")
                worker_db.commit()
            except BaseException as exc:  # surfaced in the main thread below
                worker_db.rollback()
                worker_errors.append(exc)
            finally:
                worker_finished.set()
                worker_db.close()

        worker = threading.Thread(
            target=run_reconciliation,
            name="loyalty-profile-lock-smoke",
        )
        worker.start()

        if not profile_lock_attempted.wait(timeout=2):
            raise AssertionError("Cart loyalty never attempted the CrmProfile row lock")
        if holds_lock_entered.is_set():
            raise AssertionError("Cart loyalty entered hold locking before acquiring CrmProfile")
        if worker_finished.is_set():
            raise AssertionError("Cart loyalty did not block on the CrmProfile row lock")

        # Decisive regression check: with the old hold -> profile sequence the
        # worker would already own this redemption hold and NOWAIT would fail.
        locked_hold = (
            locker_db.query(LoyaltyRedemptionHold)
            .filter(LoyaltyRedemptionHold.id == hold_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_hold.cart_id == cart_id
        assert not holds_lock_entered.is_set()

        locker_db.commit()

        worker.join(timeout=6)
        if worker.is_alive():
            raise AssertionError("Loyalty worker did not finish after CrmProfile lock release")
        if worker_errors:
            raise worker_errors[0]
        if not holds_lock_entered.is_set():
            raise AssertionError("Cart loyalty never reached LoyaltyRedemptionHold locking")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "customer_id": customer_id,
                    "cart_id": cart_id,
                    "profile_id": profile_id,
                    "hold_id": hold_id,
                    "lock_order": ["crm_profile", "loyalty_redemption_hold"],
                },
                indent=2,
            )
        )
        return 0
    finally:
        cart_adjustments._locked_loyalty_profile = original_profile_lock
        cart_adjustments._reserved_holds = original_holds_lock

        try:
            locker_db.rollback()
        finally:
            locker_db.close()
        seed_db.close()

        if customer_id is not None:
            cleanup_db = SessionLocal()
            try:
                if hold_id is not None:
                    cleanup_db.query(LoyaltyRedemptionHold).filter(
                        LoyaltyRedemptionHold.id == hold_id
                    ).delete(synchronize_session=False)
                if cart_id is not None:
                    cleanup_db.query(Cart).filter(Cart.id == cart_id).delete(
                        synchronize_session=False
                    )
                if profile_id is not None:
                    cleanup_db.query(CrmProfile).filter(CrmProfile.id == profile_id).delete(
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
