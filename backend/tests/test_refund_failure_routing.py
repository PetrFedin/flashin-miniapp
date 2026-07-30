from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.api.returns import _mark_retry_required, _mark_review_required
from backend.database import Base
from backend.models import Customer, Order, ReturnRequest
from backend.return_statuses import (
    PENDING_RETURN_STATUS,
    RETRY_REQUIRED_RETURN_STATUS,
    REVIEW_REQUIRED_RETURN_STATUS,
)
from backend.services.provider_failures import is_retryable_yookassa_error
from backend.services.refund_state import apply_provider_refund_status


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _processing_refund(db, *, suffix: str = "1") -> tuple[Order, ReturnRequest]:
    customer = Customer(telegram_id=f"refund-routing-{suffix}")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="refund_requested",
        payment_status="refund_processing",
        delivery_status="not_started",
        total_amount=100,
        delivery_price=0,
        discount_amount=0,
        loyalty_points_redeemed=0,
        loyalty_discount_amount=0,
        currency="RUB",
        delivery_type="pickup",
        address="",
        comment="",
        tracking_number="",
    )
    db.add(order)
    db.flush()
    request = ReturnRequest(
        order_id=order.id,
        customer_id=customer.id,
        reason="Item does not fit",
        status="processing",
        provider_refund_id="",
        refund_amount=100,
    )
    db.add(request)
    db.commit()
    return order, request


@pytest.mark.parametrize(
    "detail",
    [
        {"provider": "yookassa", "error": "network_error"},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 429},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 500},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 502},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 503},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 504},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": "503"},
    ],
)
def test_transient_provider_failures_are_retryable(detail):
    assert is_retryable_yookassa_error(HTTPException(status_code=502, detail=detail)) is True


@pytest.mark.parametrize(
    "detail",
    [
        "YooKassa is not configured.",
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 400},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 401},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 404},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": 422},
        {"provider": "yookassa", "error": "invalid_json"},
        {"provider": "yookassa", "error": "invalid_refund_status"},
        {"provider": "yookassa", "error": "refund_amount_mismatch"},
        {"provider": "yookassa", "error": "refund_payment_mismatch"},
        {"provider": "yookassa", "error": "refund_id_mismatch"},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": True},
        {"provider": "yookassa", "error": "provider_rejected_request", "status_code": "invalid"},
    ],
)
def test_permanent_or_contract_failures_require_review(detail):
    assert is_retryable_yookassa_error(HTTPException(status_code=502, detail=detail)) is False


def test_retry_transition_does_not_claim_provider_refund_is_pending():
    db = _session()
    order, request = _processing_refund(db)

    _mark_retry_required(db, request.id, order.id)
    db.expire_all()

    saved_request = db.query(ReturnRequest).filter(ReturnRequest.id == request.id).one()
    saved_order = db.query(Order).filter(Order.id == order.id).one()
    assert saved_request.status == RETRY_REQUIRED_RETURN_STATUS
    assert saved_request.provider_refund_id == ""
    assert saved_order.status == "refund_requested"
    assert saved_order.payment_status == "refund_retry_required"
    assert saved_order.payment_status != "refund_pending"


def test_review_transition_preserves_authoritative_provider_refund_id():
    db = _session()
    order, request = _processing_refund(db, suffix="2")

    _mark_review_required(db, request.id, order.id, "refund-provider-2")
    db.expire_all()

    saved_request = db.query(ReturnRequest).filter(ReturnRequest.id == request.id).one()
    saved_order = db.query(Order).filter(Order.id == order.id).one()
    assert saved_request.status == REVIEW_REQUIRED_RETURN_STATUS
    assert saved_request.provider_refund_id == "refund-provider-2"
    assert saved_order.status == "refund_requested"
    assert saved_order.payment_status == "refund_review_required"


def test_pending_state_is_reserved_for_authoritative_provider_pending_response():
    db = _session()
    order, request = _processing_refund(db, suffix="3")
    request.provider_refund_id = "refund-provider-3"

    result = apply_provider_refund_status(db, request, order, "pending")
    db.commit()
    db.expire_all()

    saved_request = db.query(ReturnRequest).filter(ReturnRequest.id == request.id).one()
    saved_order = db.query(Order).filter(Order.id == order.id).one()
    assert result == {}
    assert saved_request.status == PENDING_RETURN_STATUS
    assert saved_request.provider_refund_id == "refund-provider-3"
    assert saved_order.payment_status == "refund_pending"
