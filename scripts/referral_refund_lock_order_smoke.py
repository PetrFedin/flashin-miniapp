#!/usr/bin/env python3
"""Prove referral full-refund locking reaches ReferralCode before referrer CrmProfile.

The reachable deadlock guarded here spans two different invited orders using one
reusable referral code:

* payment settlement: ReferralAttribution -> ReferralCode -> CrmProfile(referrer)
* full refund (old): CrmProfile(referrer) -> ReferralAttribution -> ReferralCode

One PostgreSQL transaction holds the shared ReferralCode while a worker executes
the real full-refund loyalty adjustment for the first invited order. The worker
must block before entering referral reward reversal, leaving the exact referrer
CrmProfile NOWAIT-lockable by the first transaction. Under the old sequence the
worker would already own that profile and the NOWAIT assertion would fail.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal
from backend.models import (
    CrmProfile,
    Customer,
    LoyaltyTransaction,
    Order,
    ReferralAttribution,
    ReferralCode,
)
from backend.services import refund_loyalty


def main() -> int:
    token = uuid.uuid4().hex[:16]
    seed_db = SessionLocal()
    locker_db = SessionLocal()
    created_customer_ids: list[int] = []
    created_order_ids: list[int] = []
    created_attribution_ids: list[int] = []
    referral_id: int | None = None
    profile_id: int | None = None

    original_root_lock = refund_loyalty._locked_referral_reward_root
    original_reverse = refund_loyalty._reverse_transaction
    root_lock_attempted = threading.Event()
    referral_reverse_entered = threading.Event()
    worker_finished = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        referrer = Customer(
            telegram_id=f"ref-refund-owner-{token}",
            username=f"ref_refund_owner_{token}",
            first_name="ReferralOwner",
        )
        invited_a = Customer(
            telegram_id=f"ref-refund-a-{token}",
            username=f"ref_refund_a_{token}",
            first_name="InvitedA",
        )
        invited_b = Customer(
            telegram_id=f"ref-refund-b-{token}",
            username=f"ref_refund_b_{token}",
            first_name="InvitedB",
        )
        seed_db.add_all([referrer, invited_a, invited_b])
        seed_db.flush()
        created_customer_ids = [int(referrer.id), int(invited_a.id), int(invited_b.id)]

        referral = ReferralCode(
            customer_id=referrer.id,
            code=f"LR{token[:10]}".upper(),
            reward_points=250.0,
            used_count=1,
            active=True,
        )
        profile = CrmProfile(
            customer_id=referrer.id,
            segment="referrer",
            loyalty_points=250.0,
        )
        seed_db.add_all([referral, profile])
        seed_db.flush()
        referral_id = int(referral.id)
        profile_id = int(profile.id)

        refunded_order = Order(
            customer_id=invited_a.id,
            status="refunded",
            payment_status="refunded",
            total_amount=1000.0,
            currency="RUB",
            referral_code=referral.code,
        )
        settling_order = Order(
            customer_id=invited_b.id,
            status="paid",
            payment_status="paid",
            total_amount=1000.0,
            currency="RUB",
            referral_code=referral.code,
        )
        seed_db.add_all([refunded_order, settling_order])
        seed_db.flush()
        created_order_ids = [int(refunded_order.id), int(settling_order.id)]

        rewarded_attribution = ReferralAttribution(
            referral_code_id=referral.id,
            invited_customer_id=invited_a.id,
            status="rewarded",
            rewarded_order_id=refunded_order.id,
        )
        competing_attribution = ReferralAttribution(
            referral_code_id=referral.id,
            invited_customer_id=invited_b.id,
            status="pending",
        )
        reward = LoyaltyTransaction(
            customer_id=referrer.id,
            order_id=refunded_order.id,
            points_delta=250.0,
            reason="referral_reward",
        )
        seed_db.add_all([rewarded_attribution, competing_attribution, reward])
        seed_db.commit()
        created_attribution_ids = [
            int(rewarded_attribution.id),
            int(competing_attribution.id),
        ]

        refunded_order_id = int(refunded_order.id)
        settling_order_id = int(settling_order.id)
        invited_a_id = int(invited_a.id)
        referrer_id = int(referrer.id)

        def observed_root_lock(db, *, order_id):
            root_lock_attempted.set()
            return original_root_lock(db, order_id=order_id)

        def observed_reverse(
            db,
            *,
            customer_id,
            order_id,
            original_reason,
            reversal_reason,
        ):
            if original_reason == "referral_reward":
                referral_reverse_entered.set()
            return original_reverse(
                db,
                customer_id=customer_id,
                order_id=order_id,
                original_reason=original_reason,
                reversal_reason=reversal_reason,
            )

        refund_loyalty._locked_referral_reward_root = observed_root_lock
        refund_loyalty._reverse_transaction = observed_reverse

        locker_db.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked_referral = (
            locker_db.query(ReferralCode)
            .filter(ReferralCode.id == referral_id)
            .with_for_update()
            .one()
        )
        assert locked_referral.customer_id == referrer_id

        def run_refund_loyalty() -> None:
            worker_db = SessionLocal()
            try:
                worker_db.execute(text("SET LOCAL lock_timeout = '3s'"))
                worker_db.execute(text("SET LOCAL statement_timeout = '5s'"))
                result = refund_loyalty.apply_full_refund_loyalty(
                    worker_db,
                    customer_id=invited_a_id,
                    order_id=refunded_order_id,
                    redeemed_points=0,
                )
                assert result["referral_rewards"]
                worker_db.commit()
            except BaseException as exc:  # surfaced in the main thread below
                worker_db.rollback()
                worker_errors.append(exc)
            finally:
                worker_finished.set()
                worker_db.close()

        worker = threading.Thread(
            target=run_refund_loyalty,
            name="referral-refund-code-lock-smoke",
        )
        worker.start()

        if not root_lock_attempted.wait(timeout=2):
            raise AssertionError("Refund loyalty never attempted the referral root lock")
        time.sleep(0.15)
        if worker_finished.is_set():
            raise AssertionError("Refund loyalty did not block on the held ReferralCode row")
        if referral_reverse_entered.is_set():
            raise AssertionError(
                "Refund loyalty entered referral reward reversal before acquiring ReferralCode"
            )

        # Decisive regression check: the old refund sequence acquired the
        # referrer's profile before attempting ReferralCode, so this NOWAIT lock
        # would fail while the worker waited on the code row.
        locked_profile = (
            locker_db.query(CrmProfile)
            .filter(CrmProfile.id == profile_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_profile.customer_id == referrer_id
        assert not referral_reverse_entered.is_set()

        locker_db.commit()

        worker.join(timeout=6)
        if worker.is_alive():
            raise AssertionError("Refund loyalty worker did not finish after ReferralCode release")
        if worker_errors:
            raise worker_errors[0]
        if not referral_reverse_entered.is_set():
            raise AssertionError("Refund loyalty never reached referral reward reversal")

        verify_db = SessionLocal()
        try:
            persisted_attribution = (
                verify_db.query(ReferralAttribution)
                .filter(ReferralAttribution.id == created_attribution_ids[0])
                .one()
            )
            persisted_referral = (
                verify_db.query(ReferralCode).filter(ReferralCode.id == referral_id).one()
            )
            persisted_profile = (
                verify_db.query(CrmProfile).filter(CrmProfile.id == profile_id).one()
            )
            reversal = (
                verify_db.query(LoyaltyTransaction)
                .filter(
                    LoyaltyTransaction.customer_id == referrer_id,
                    LoyaltyTransaction.order_id == refunded_order_id,
                    LoyaltyTransaction.reason == "referral_refund_reversal",
                )
                .one()
            )
            assert persisted_attribution.status == "reversed"
            assert persisted_referral.used_count == 0
            assert float(persisted_profile.loyalty_points) == 0.0
            assert float(reversal.points_delta) == -250.0
        finally:
            verify_db.close()

        print(
            json.dumps(
                {
                    "status": "ok",
                    "referrer_id": referrer_id,
                    "referral_code_id": referral_id,
                    "refunded_order_id": refunded_order_id,
                    "competing_order_id": settling_order_id,
                    "lock_order": [
                        "referral_attribution",
                        "referral_code",
                        "crm_profile",
                    ],
                },
                indent=2,
            )
        )
        return 0
    finally:
        refund_loyalty._locked_referral_reward_root = original_root_lock
        refund_loyalty._reverse_transaction = original_reverse

        try:
            locker_db.rollback()
        finally:
            locker_db.close()
        seed_db.close()

        if created_customer_ids:
            cleanup_db = SessionLocal()
            try:
                if created_order_ids:
                    cleanup_db.query(LoyaltyTransaction).filter(
                        LoyaltyTransaction.order_id.in_(created_order_ids)
                    ).delete(synchronize_session=False)
                if created_attribution_ids:
                    cleanup_db.query(ReferralAttribution).filter(
                        ReferralAttribution.id.in_(created_attribution_ids)
                    ).delete(synchronize_session=False)
                if referral_id is not None:
                    cleanup_db.query(ReferralCode).filter(
                        ReferralCode.id == referral_id
                    ).delete(synchronize_session=False)
                if profile_id is not None:
                    cleanup_db.query(CrmProfile).filter(
                        CrmProfile.id == profile_id
                    ).delete(synchronize_session=False)
                if created_order_ids:
                    cleanup_db.query(Order).filter(Order.id.in_(created_order_ids)).delete(
                        synchronize_session=False
                    )
                cleanup_db.query(Customer).filter(
                    Customer.id.in_(created_customer_ids)
                ).delete(synchronize_session=False)
                cleanup_db.commit()
            except BaseException:
                cleanup_db.rollback()
                raise
            finally:
                cleanup_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
