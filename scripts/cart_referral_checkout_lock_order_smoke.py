#!/usr/bin/env python3
"""Prove cart referral mutation cannot lock Cart before checkout's Customer lock.

The deadlock being guarded against is:

* checkout:       Customer -> Cart
* referral (old): Cart -> Customer

The referral endpoint has an initial get/create-cart transaction that commits
before its mutation phase. This smoke therefore exercises the real second-phase
helper directly. One PostgreSQL transaction holds the invited Customer row while
a worker runs the real referral attach + Cart lock helper. The worker must block
at Customer, leaving Cart available to the checkout-side transaction via NOWAIT.
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

from backend.api import cart as cart_api
from backend.database import SessionLocal
from backend.models import Cart, Customer, ReferralAttribution, ReferralCode


def main() -> int:
    token = uuid.uuid4().hex[:16]
    seed_db = SessionLocal()
    locker_db = SessionLocal()
    inviter_id = None
    invited_id = None
    cart_id = None
    referral_id = None

    original_attach = cart_api.attach_referral_to_customer
    original_lock_cart = cart_api._lock_cart
    customer_lock_attempted = threading.Event()
    cart_lock_entered = threading.Event()
    worker_finished = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        inviter = Customer(
            telegram_id=f"ref-lock-inviter-{token}",
            username=f"ref_lock_inviter_{token}",
            first_name="Inviter",
        )
        invited = Customer(
            telegram_id=f"ref-lock-invited-{token}",
            username=f"ref_lock_invited_{token}",
            first_name="Invited",
        )
        seed_db.add_all([inviter, invited])
        seed_db.flush()

        referral = ReferralCode(
            customer_id=inviter.id,
            code=f"FL{token[:8]}".upper(),
            reward_points=250,
            used_count=0,
            active=True,
        )
        cart = Cart(customer_id=invited.id, status="active")
        seed_db.add_all([referral, cart])
        seed_db.commit()

        inviter_id = int(inviter.id)
        invited_id = int(invited.id)
        referral_id = int(referral.id)
        cart_id = int(cart.id)
        referral_code = str(referral.code)

        def observed_attach(db, code, customer_id):
            customer_lock_attempted.set()
            return original_attach(db, code, customer_id)

        def observed_lock_cart(db, locked_cart_id):
            cart_lock_entered.set()
            return original_lock_cart(db, locked_cart_id)

        cart_api.attach_referral_to_customer = observed_attach
        cart_api._lock_cart = observed_lock_cart

        locker_db.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked_customer = (
            locker_db.query(Customer)
            .filter(Customer.id == invited_id)
            .with_for_update()
            .one()
        )
        assert locked_customer.id == invited_id

        def run_referral_mutation() -> None:
            worker_db = SessionLocal()
            try:
                worker_db.execute(text("SET LOCAL lock_timeout = '3s'"))
                worker_db.execute(text("SET LOCAL statement_timeout = '5s'"))
                locked_cart = cart_api._attach_referral_then_lock_cart(
                    worker_db,
                    referral_code,
                    customer_id=invited_id,
                    cart_id=cart_id,
                )
                locked_cart.referral_code = referral_code
                worker_db.commit()
            except BaseException as exc:  # surfaced in the main thread below
                worker_db.rollback()
                worker_errors.append(exc)
            finally:
                worker_finished.set()
                worker_db.close()

        worker = threading.Thread(target=run_referral_mutation, name="referral-cart-lock-smoke")
        worker.start()

        if not customer_lock_attempted.wait(timeout=2):
            raise AssertionError("Referral mutation never attempted the customer row lock")
        if cart_lock_entered.is_set():
            raise AssertionError("Referral mutation entered Cart lock before acquiring Customer")
        if worker_finished.is_set():
            raise AssertionError("Referral mutation did not block on the customer row lock")

        # This is the decisive regression check. Under the old Cart -> Customer
        # order the worker would already own Cart and NOWAIT would fail here.
        locked_cart = (
            locker_db.query(Cart)
            .filter(Cart.id == cart_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_cart.id == cart_id
        assert not cart_lock_entered.is_set()

        locker_db.commit()

        worker.join(timeout=6)
        if worker.is_alive():
            raise AssertionError("Referral worker did not finish after Customer lock release")
        if worker_errors:
            raise worker_errors[0]
        if not cart_lock_entered.is_set():
            raise AssertionError("Referral mutation never reached the Cart lock")

        verify_db = SessionLocal()
        try:
            persisted_cart = verify_db.query(Cart).filter(Cart.id == cart_id).one()
            attribution = (
                verify_db.query(ReferralAttribution)
                .filter(ReferralAttribution.invited_customer_id == invited_id)
                .one()
            )
            assert persisted_cart.referral_code == referral_code
            assert attribution.referral_code_id == referral_id
            assert attribution.status == "pending"
        finally:
            verify_db.close()

        print(
            json.dumps(
                {
                    "status": "ok",
                    "invited_customer_id": invited_id,
                    "cart_id": cart_id,
                    "referral_id": referral_id,
                    "lock_order": ["customer", "cart"],
                },
                indent=2,
            )
        )
        return 0
    finally:
        cart_api.attach_referral_to_customer = original_attach
        cart_api._lock_cart = original_lock_cart

        try:
            locker_db.rollback()
        finally:
            locker_db.close()
        seed_db.close()

        if invited_id is not None:
            cleanup_db = SessionLocal()
            try:
                cleanup_db.query(ReferralAttribution).filter(
                    ReferralAttribution.invited_customer_id == invited_id
                ).delete(synchronize_session=False)
                cleanup_db.query(Cart).filter(Cart.id == cart_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(ReferralCode).filter(ReferralCode.id == referral_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(Customer).filter(
                    Customer.id.in_([inviter_id, invited_id])
                ).delete(synchronize_session=False)
                cleanup_db.commit()
            except BaseException:
                cleanup_db.rollback()
                raise
            finally:
                cleanup_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
