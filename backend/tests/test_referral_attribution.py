from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.models import Customer, Order, ReferralAttribution, ReferralCode
from backend.services.loyalty import apply_referral, attach_referral_to_customer


class FakeQuery:
    def __init__(self, session, entity):
        self.session = session
        self.entity = entity

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        if self.entity is Customer:
            return self.session.customer
        if self.entity is Order:
            return self.session.settled_order
        if self.entity is ReferralCode:
            return self.session.referral
        if self.entity is ReferralAttribution:
            return self.session.attribution
        return None


class FakeSession:
    def __init__(
        self,
        *,
        customer=None,
        settled_order=None,
        referral=None,
        attribution=None,
    ):
        self.customer = customer or SimpleNamespace(id=7)
        self.settled_order = settled_order
        self.referral = referral
        self.attribution = attribution
        self.added = []

    def query(self, entity):
        return FakeQuery(self, entity)

    def add(self, value):
        self.added.append(value)


def referral(*, referral_id=10, owner_id=1):
    return SimpleNamespace(id=referral_id, customer_id=owner_id, active=True)


def attribution(*, referral_id=10, invited_id=7, status="pending"):
    return SimpleNamespace(
        id=20,
        referral_code_id=referral_id,
        invited_customer_id=invited_id,
        status=status,
    )


def test_first_referral_code_creates_pending_attribution():
    db = FakeSession(referral=referral())

    assert attach_referral_to_customer(db, " code10 ", 7) is True
    assert len(db.added) == 1
    assert isinstance(db.added[0], ReferralAttribution)
    assert db.added[0].referral_code_id == 10
    assert db.added[0].invited_customer_id == 7
    assert db.added[0].status == "pending"


def test_legacy_apply_referral_only_attaches_pending_attribution():
    db = FakeSession(referral=referral())

    assert apply_referral(db, "CODE10", 7) is True
    assert len(db.added) == 1
    assert isinstance(db.added[0], ReferralAttribution)
    assert db.added[0].status == "pending"


def test_same_referral_retry_is_idempotent_before_first_paid_order():
    existing = attribution(referral_id=10)
    db = FakeSession(referral=referral(referral_id=10), attribution=existing)

    assert attach_referral_to_customer(db, "CODE10", 7) is True
    assert db.added == []
    assert existing.status == "pending"


def test_referral_after_settled_order_is_rejected_even_for_same_code():
    db = FakeSession(
        settled_order=SimpleNamespace(id=99, payment_status="paid"),
        referral=referral(referral_id=10),
        attribution=attribution(referral_id=10, status="rewarded"),
    )

    with pytest.raises(HTTPException) as error:
        attach_referral_to_customer(db, "CODE10", 7)

    assert error.value.status_code == 409
    assert error.value.detail == "Referral code must be applied before the first paid order"
    assert db.added == []


def test_different_referral_after_attribution_is_conflict():
    db = FakeSession(
        referral=referral(referral_id=11),
        attribution=attribution(referral_id=10),
    )

    with pytest.raises(HTTPException) as error:
        attach_referral_to_customer(db, "CODE11", 7)

    assert error.value.status_code == 409
    assert error.value.detail == "Customer is already linked to another referral code"
    assert db.added == []


def test_missing_customer_is_not_silently_eligible():
    db = FakeSession(customer=None, referral=referral())
    db.customer = None

    with pytest.raises(HTTPException) as error:
        attach_referral_to_customer(db, "CODE10", 7)

    assert error.value.status_code == 404
    assert db.added == []


def test_missing_or_self_referral_is_unavailable():
    assert attach_referral_to_customer(FakeSession(referral=None), "UNKNOWN", 7) is False
    assert attach_referral_to_customer(
        FakeSession(referral=referral(owner_id=7)),
        "SELF",
        7,
    ) is False
