from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.database import SessionLocal
from backend.models import Customer, Order
from backend.payment_attempt_models import PaymentCreationAttempt
from backend.payment_attempt_statuses import (
    ABANDONED_PAYMENT_ATTEMPT_STATUS,
    COMPLETED_PAYMENT_ATTEMPT_STATUS,
    CREATING_PAYMENT_ATTEMPT_STATUS,
    RETRY_REQUIRED_PAYMENT_ATTEMPT_STATUS,
    REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS,
)
from backend.services.order_cancellation import _validate_cancellation_state
from backend.services.payment_creation import (
    begin_payment_creation,
    complete_payment_creation_from_provider,
    finalize_payment_creation,
    mark_payment_creation_retry_required,
    mark_payment_creation_review_required,
)
from backend.services.provider_failures import is_retryable_yookassa_error


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _order(db, *, amount: float = 1250.0) -> tuple[Customer, Order]:
    customer = Customer(telegram_id=f"payment-attempt-{uuid4().hex}")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="created",
        payment_status="pending",
        total_amount=amount,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    return customer, order


def _attempt(db, attempt_id: int) -> PaymentCreationAttempt:
    return db.query(PaymentCreationAttempt).filter(PaymentCreationAttempt.id == attempt_id).one()


def test_retry_required_reuses_same_durable_attempt(db):
    customer, order = _order(db)
    first = begin_payment_creation(db, order.id, customer.id, lease_seconds=30)

    mark_payment_creation_retry_required(db, first.attempt_id, "network_error")
    db.flush()
    assert _attempt(db, first.attempt_id).status == RETRY_REQUIRED_PAYMENT_ATTEMPT_STATUS

    retried = begin_payment_creation(db, order.id, customer.id, lease_seconds=30)

    assert retried.attempt_id == first.attempt_id
    assert retried.attempt_number == first.attempt_number
    assert _attempt(db, first.attempt_id).status == CREATING_PAYMENT_ATTEMPT_STATUS


def test_active_creation_lease_blocks_parallel_attempt(db):
    customer, order = _order(db)
    begin_payment_creation(db, order.id, customer.id, lease_seconds=30)

    with pytest.raises(HTTPException) as exc:
        begin_payment_creation(db, order.id, customer.id, lease_seconds=30)

    assert exc.value.status_code == 409
    assert exc.value.headers
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_review_required_blocks_automatic_new_attempt(db):
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id, lease_seconds=30)
    mark_payment_creation_review_required(db, claim.attempt_id, "provider_contract_uncertain")
    db.flush()

    with pytest.raises(HTTPException) as exc:
        begin_payment_creation(db, order.id, customer.id, lease_seconds=30)

    assert exc.value.status_code == 409
    assert "manual review" in str(exc.value.detail).lower()


def test_finalize_marks_attempt_completed_and_order_payment_created(db):
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id, lease_seconds=30)

    finalized_order, payment = finalize_payment_creation(
        db,
        claim.attempt_id,
        "provider-payment-complete",
        "pending",
        "https://payments.example/confirm",
    )
    db.flush()

    attempt = _attempt(db, claim.attempt_id)
    assert finalized_order.status == "payment_created"
    assert finalized_order.payment_status == "payment_created"
    assert payment.provider_payment_id == "provider-payment-complete"
    assert attempt.status == COMPLETED_PAYMENT_ATTEMPT_STATUS
    assert attempt.provider_payment_id == "provider-payment-complete"
    assert attempt.lease_expires_at is None
    assert attempt.last_error == ""


def test_provider_canceled_abandons_attempt_and_allows_fresh_attempt(db):
    customer, order = _order(db)
    first = begin_payment_creation(db, order.id, customer.id, lease_seconds=30)

    finalized_order, payment = finalize_payment_creation(
        db,
        first.attempt_id,
        "provider-payment-canceled",
        "canceled",
        "",
    )
    db.flush()

    abandoned = _attempt(db, first.attempt_id)
    assert payment.status == "canceled"
    assert finalized_order.status == "created"
    assert finalized_order.payment_status == "pending"
    assert abandoned.status == ABANDONED_PAYMENT_ATTEMPT_STATUS
    assert abandoned.last_error == "provider_canceled"
    assert abandoned.lease_expires_at is None

    second = begin_payment_creation(db, order.id, customer.id, lease_seconds=30)
    assert second.attempt_id != first.attempt_id
    assert second.attempt_number == first.attempt_number + 1


def test_verified_provider_webhook_converges_retryable_attempt(db):
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id, lease_seconds=30)
    mark_payment_creation_retry_required(db, claim.attempt_id, "timeout")
    db.flush()

    complete_payment_creation_from_provider(db, order.id, "provider-webhook-win")
    db.flush()

    attempt = _attempt(db, claim.attempt_id)
    assert attempt.status == COMPLETED_PAYMENT_ATTEMPT_STATUS
    assert attempt.provider_payment_id == "provider-webhook-win"
    assert attempt.last_error == ""


def test_review_attempt_only_converges_on_matching_verified_provider_id(db):
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id, lease_seconds=30)
    mark_payment_creation_review_required(
        db,
        claim.attempt_id,
        "provider_response_uncertain",
        provider_payment_id="provider-expected",
    )
    db.flush()

    complete_payment_creation_from_provider(db, order.id, "provider-other")
    db.flush()
    assert _attempt(db, claim.attempt_id).status == REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS

    complete_payment_creation_from_provider(db, order.id, "provider-expected")
    db.flush()
    attempt = _attempt(db, claim.attempt_id)
    assert attempt.status == COMPLETED_PAYMENT_ATTEMPT_STATUS
    assert attempt.provider_payment_id == "provider-expected"


def test_manual_cancellation_rejects_unresolved_payment_creation(db):
    customer, order = _order(db)
    begin_payment_creation(db, order.id, customer.id, lease_seconds=30)

    with pytest.raises(HTTPException) as exc:
        _validate_cancellation_state(db, order, "manual")

    assert exc.value.status_code == 409
    assert "payment creation" in str(exc.value.detail).lower()


@pytest.mark.parametrize("status_code", [408, 409, 425, 429, 500, 502, 503, 504])
def test_transient_provider_http_failures_are_retryable(status_code):
    exc = HTTPException(
        status_code=502,
        detail={"provider": "yookassa", "error": "provider_error", "status_code": status_code},
    )
    assert is_retryable_yookassa_error(exc) is True


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_deterministic_provider_rejections_require_review(status_code):
    exc = HTTPException(
        status_code=502,
        detail={"provider": "yookassa", "error": "provider_error", "status_code": status_code},
    )
    assert is_retryable_yookassa_error(exc) is False


def test_network_provider_failure_is_retryable():
    exc = HTTPException(
        status_code=503,
        detail={"provider": "yookassa", "error": "network_error"},
    )
    assert is_retryable_yookassa_error(exc) is True
