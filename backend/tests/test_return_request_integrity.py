from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import ReturnRequest
from backend.return_statuses import (
    FINAL_RETURN_STATUSES,
    OPEN_RETURN_STATUSES,
    VALID_RETURN_STATUSES,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _request(**overrides):
    values = {
        "order_id": 1,
        "customer_id": 1,
        "reason": "Item does not fit",
        "status": "requested",
        "provider_refund_id": "",
        "refund_amount": 0,
    }
    values.update(overrides)
    return ReturnRequest(**values)


def _direct_insert(db, **overrides):
    values = {
        "order_id": 1,
        "customer_id": 1,
        "reason": "Item does not fit",
        "status": "requested",
        "provider_refund_id": "",
        "refund_amount": 0,
    }
    values.update(overrides)
    db.execute(ReturnRequest.__table__.insert().values(**values))


def test_status_sets_are_disjoint_and_complete():
    assert OPEN_RETURN_STATUSES.isdisjoint(FINAL_RETURN_STATUSES)
    assert OPEN_RETURN_STATUSES | FINAL_RETURN_STATUSES | {"failed"} == VALID_RETURN_STATUSES


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": "bad"},
        {"reason": " valid reason "},
        {"status": "unknown"},
        {"status": "processing", "refund_amount": 0},
        {"status": "refund_retry_required", "refund_amount": 0},
        {"status": "refund_review_required", "refund_amount": 0},
        {"status": "refund_pending", "refund_amount": 10, "provider_refund_id": ""},
        {"status": "approved", "refund_amount": 10, "provider_refund_id": ""},
        {"status": "approved_partial", "refund_amount": 0, "provider_refund_id": "refund-1"},
        {"provider_refund_id": " refund-1 "},
    ],
)
def test_database_rejects_impossible_return_states(overrides):
    db = _session()
    with pytest.raises(IntegrityError):
        _direct_insert(db, **overrides)
        db.commit()
    db.rollback()


def test_requested_and_failed_states_allow_zero_refund_without_provider_id():
    db = _session()
    db.add_all([_request(order_id=1), _request(order_id=2, status="failed")])
    db.commit()

    assert db.query(ReturnRequest).count() == 2


@pytest.mark.parametrize("status", sorted(FINAL_RETURN_STATUSES | {"refund_pending"}))
def test_provider_linked_states_require_positive_amount_and_provider_id(status):
    db = _session()
    request = _request(
        order_id=10,
        status=status,
        refund_amount=25.5,
        provider_refund_id="refund-10",
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    assert request.refund_amount == 25.5
    assert request.provider_refund_id == "refund-10"


def test_only_one_open_return_request_can_exist_per_order():
    db = _session()
    db.add(_request(order_id=20, status="requested"))
    db.commit()

    db.add(_request(order_id=20, status="processing", refund_amount=10))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    rows = db.query(ReturnRequest).filter(ReturnRequest.order_id == 20).all()
    assert len(rows) == 1
    assert rows[0].status == "requested"


def test_closed_request_does_not_block_a_new_open_request():
    db = _session()
    db.add(_request(order_id=30, status="failed"))
    db.commit()
    db.add(_request(order_id=30, status="requested"))
    db.commit()

    assert db.query(ReturnRequest).filter(ReturnRequest.order_id == 30).count() == 2


def test_metadata_contains_return_constraints_and_open_index():
    constraint_names = {constraint.name for constraint in ReturnRequest.__table__.constraints}
    index_names = {index.name for index in ReturnRequest.__table__.indexes}

    assert {
        "ck_return_requests_refund_nonnegative",
        "ck_return_requests_reason_length",
        "ck_return_requests_reason_normalized",
        "ck_return_requests_status_valid",
        "ck_return_requests_amount_required",
        "ck_return_requests_provider_id_normalized",
        "ck_return_requests_provider_id_required",
    }.issubset(constraint_names)
    assert "uq_return_requests_one_open_per_order" in index_names
    assert "uq_return_requests_provider_refund_id" in index_names


def test_return_migration_repairs_legacy_rows_before_constraints():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0021_return_request_integrity.py"
    ).read_text(encoding="utf-8")

    repair_position = source.index("UPDATE return_requests")
    dedupe_position = source.index("WITH ranked AS")
    constraint_position = source.index("op.create_check_constraint")
    index_position = source.index("op.create_index")

    assert repair_position < constraint_position
    assert dedupe_position < index_position
    assert "row_number() OVER" in source
    assert "uq_return_requests_one_open_per_order" in source
