import os
from types import SimpleNamespace

import pytest

from backend.models import LoyaltyTransaction, ReferralAttribution, ReferralCode
from backend.services import refund_loyalty


class RecordingQuery:
    def __init__(self, session, entity):
        self.session = session
        self.entity = entity

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def first(self):
        if self.entity is ReferralAttribution:
            self.session.events.append("referral_attribution")
            return self.session.attribution
        if self.entity is ReferralCode:
            self.session.events.append("referral_code")
            return self.session.referral
        return None

    def all(self):
        if self.entity is LoyaltyTransaction:
            self.session.events.append("referral_rewards")
            return list(self.session.rewards)
        return []


class RecordingSession:
    def __init__(self, *, attribution=None, referral=None, rewards=None):
        self.attribution = attribution
        self.referral = referral
        self.rewards = list(rewards or [])
        self.events: list[str] = []

    def query(self, entity):
        return RecordingQuery(self, entity)


def test_refund_referral_root_locks_attribution_before_code():
    attribution = SimpleNamespace(referral_code_id=31, status="rewarded")
    referral = SimpleNamespace(id=31, used_count=4)
    db = RecordingSession(attribution=attribution, referral=referral)

    locked_attribution, locked_referral = refund_loyalty._locked_referral_reward_root(
        db,
        order_id=17,
    )

    assert db.events == ["referral_attribution", "referral_code"]
    assert locked_attribution is attribution
    assert locked_referral is referral


def test_missing_refund_attribution_does_not_lock_unbound_referral_code():
    db = RecordingSession(attribution=None, referral=SimpleNamespace(id=31))

    locked_attribution, locked_referral = refund_loyalty._locked_referral_reward_root(
        db,
        order_id=17,
    )

    assert db.events == ["referral_attribution"]
    assert locked_attribution is None
    assert locked_referral is None


def test_full_refund_locks_referral_root_before_any_reward_profile_mutation(monkeypatch):
    attribution = SimpleNamespace(referral_code_id=31, status="rewarded")
    referral = SimpleNamespace(id=31, used_count=2)
    reward = SimpleNamespace(customer_id=9)
    db = RecordingSession(
        attribution=attribution,
        referral=referral,
        rewards=[reward],
    )
    events: list[str] = []

    def lock_root(_db, *, order_id):
        assert order_id == 17
        events.append("referral_root")
        return attribution, referral

    def reverse(_db, *, customer_id, order_id, original_reason, reversal_reason):
        events.append(f"reverse:{original_reason}:{customer_id}")
        assert order_id == 17
        return {
            "target": 10.0,
            "reversed": 10.0,
            "unrecovered": 0.0,
            "idempotent": False,
        }

    monkeypatch.setattr(refund_loyalty, "_locked_referral_reward_root", lock_root)
    monkeypatch.setattr(refund_loyalty, "_reverse_transaction", reverse)
    monkeypatch.setattr(refund_loyalty, "refund_redeemed_points", lambda *args, **kwargs: None)

    result = refund_loyalty.apply_full_refund_loyalty(
        db,
        customer_id=7,
        order_id=17,
        redeemed_points=0,
    )

    assert events == [
        "referral_root",
        "reverse:order_paid:7",
        "reverse:referral_reward:9",
    ]
    assert db.events == ["referral_rewards"]
    assert attribution.status == "reversed"
    assert referral.used_count == 1
    assert result["redeemed_points_restored"] == 0.0
    assert result["referral_rewards"][0]["customer_id"] == 9


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires PostgreSQL row-lock semantics",
)
def test_postgres_referral_refund_code_first_lock_order_smoke():
    from scripts.referral_refund_lock_order_smoke import main

    assert main() == 0
