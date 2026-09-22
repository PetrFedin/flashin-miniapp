from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import refund_allocation_models as _refund_allocation_models  # noqa: F401
from backend.database import Base, utcnow_naive
from backend.models import Order, OrderItem, ReturnRequest
from backend.refund_allocation_models import ReturnRefundAllocation
from backend.reverse_logistics_models import (
    ReturnLogisticsCase,
    ReturnLogisticsEvent,
    ReturnLogisticsItem,
)
from backend.services.moysklad_outbound import _allocate_net_line_totals
from backend.services.order_money_allocation import allocate_order_money
from backend.services.refund_allocation import (
    RefundAllocationError,
    ensure_refund_allocation,
    reconcile_refund_allocations,
    refund_allocation_options,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()


def _order(
    db,
    *,
    total=1850.0,
    delivery=150.0,
    discount=199.99,
    loyalty=100.0,
    items=((1, 1000.0, 1), (2, 333.33, 3)),
):
    order = Order(
        customer_id=1,
        status="paid",
        payment_status="paid",
        total_amount=total,
        delivery_price=delivery,
        discount_amount=discount,
        loyalty_discount_amount=loyalty,
        loyalty_points_redeemed=loyalty,
        currency="RUB",
        delivery_type="courier",
    )
    db.add(order)
    db.flush()
    rows = []
    for suffix, price, quantity in items:
        row = OrderItem(
            order_id=order.id,
            product_id=100 + suffix,
            variant_id=200 + suffix,
            title=f"Item {suffix}",
            size="M",
            quantity=quantity,
            price=price,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return order, rows


def _ret(db, order, *, status="requested", reason="refund evidence"):
    row = ReturnRequest(
        order_id=order.id,
        customer_id=order.customer_id,
        reason=reason,
        status=status,
        refund_amount=0,
    )
    db.add(row)
    db.flush()
    return row


def _spec(kind, amount, *, item_id=None, quantity=None):
    return SimpleNamespace(
        component_kind=kind,
        order_item_id=item_id,
        quantity_evidence=quantity,
        amount=amount,
    )


def test_shared_order_money_policy_handles_promo_loyalty_delivery_and_matches_moysklad():
    db = _db()
    order, items = _order(db)

    policy = allocate_order_money(order, items)

    assert policy.order_total_cents == 185000
    assert policy.delivery_cents == 15000
    assert policy.discount_cents == 19999
    assert policy.loyalty_cents == 10000
    assert policy.merchandise_cents == 170000
    assert [line.net_cents for line in policy.lines] == [85000, 85000]

    synthetic = [(item, None, None) for item in items]
    assert _allocate_net_line_totals(order, synthetic) == [85000, 85000]


def test_partial_refund_requires_explicit_composition_and_persists_item_delivery_goodwill():
    db = _db()
    order, items = _order(db)
    ret = _ret(db, order)

    with pytest.raises(RefundAllocationError, match="Partial or goodwill"):
        ensure_refund_allocation(
            db,
            order=order,
            ret=ret,
            requested_amount=700,
            raw_allocations=[],
        )

    evidence = ensure_refund_allocation(
        db,
        order=order,
        ret=ret,
        requested_amount=700,
        raw_allocations=[
            _spec("item", 500, item_id=items[0].id, quantity=1),
            _spec("delivery", 100),
            _spec("goodwill", 100),
        ],
    )
    ret.refund_amount = 700
    ret.status = "approved_partial"
    db.commit()

    assert evidence["allocated_cents"] == 70000
    assert evidence["item_cents"] == 50000
    assert evidence["delivery_cents"] == 10000
    assert evidence["goodwill_cents"] == 10000
    assert db.query(ReturnRefundAllocation).filter(
        ReturnRefundAllocation.return_request_id == ret.id
    ).count() == 3


def test_full_remaining_refund_can_auto_allocate_exact_remaining_components():
    db = _db()
    order, items = _order(db)
    first = _ret(db, order)
    ensure_refund_allocation(
        db,
        order=order,
        ret=first,
        requested_amount=700,
        raw_allocations=[
            _spec("item", 600, item_id=items[0].id),
            _spec("delivery", 100),
        ],
    )
    first.refund_amount = 700
    first.status = "approved_partial"
    db.commit()

    second = _ret(db, order)
    evidence = ensure_refund_allocation(
        db,
        order=order,
        ret=second,
        requested_amount=1150,
        raw_allocations=[],
    )

    assert evidence["allocated_cents"] == 115000
    components = {
        (row["component_kind"], row["order_item_id"]): row["amount_cents"]
        for row in evidence["components"]
    }
    assert components[("item", items[0].id)] == 25000
    assert components[("item", items[1].id)] == 85000
    assert components[("delivery", None)] == 5000


def test_staged_allocations_cannot_exceed_original_line_delivery_or_quantity_evidence():
    db = _db()
    order, items = _order(db)
    first = _ret(db, order)
    ensure_refund_allocation(
        db,
        order=order,
        ret=first,
        requested_amount=800,
        raw_allocations=[
            _spec("item", 800, item_id=items[0].id, quantity=1),
        ],
    )
    first.refund_amount = 800
    first.status = "approved_partial"
    db.commit()

    second = _ret(db, order)
    with pytest.raises(RefundAllocationError, match="remaining value"):
        ensure_refund_allocation(
            db,
            order=order,
            ret=second,
            requested_amount=100,
            raw_allocations=[_spec("item", 100, item_id=items[0].id)],
        )

    with pytest.raises(RefundAllocationError, match="remaining sold quantity"):
        ensure_refund_allocation(
            db,
            order=order,
            ret=second,
            requested_amount=100,
            raw_allocations=[
                _spec("item", 100, item_id=items[1].id, quantity=4),
            ],
        )

    with pytest.raises(RefundAllocationError, match="delivery"):
        ensure_refund_allocation(
            db,
            order=order,
            ret=second,
            requested_amount=151,
            raw_allocations=[_spec("delivery", 151)],
        )


def test_allocation_is_immutable_across_retry_but_identical_replay_is_idempotent():
    db = _db()
    order, items = _order(db)
    ret = _ret(db, order)

    ensure_refund_allocation(
        db,
        order=order,
        ret=ret,
        requested_amount=500,
        raw_allocations=[_spec("item", 500, item_id=items[0].id)],
    )
    ret.refund_amount = 500
    ret.status = "refund_retry_required"
    db.commit()

    same = ensure_refund_allocation(
        db,
        order=order,
        ret=ret,
        requested_amount=500,
        raw_allocations=[],
    )
    assert same["item_cents"] == 50000

    with pytest.raises(RefundAllocationError, match="already fixed"):
        ensure_refund_allocation(
            db,
            order=order,
            ret=ret,
            requested_amount=500,
            raw_allocations=[_spec("goodwill", 500)],
        )


def test_failed_refund_releases_allocation_capacity_without_deleting_evidence():
    db = _db()
    order, items = _order(db)
    failed = _ret(db, order, status="failed")
    failed.refund_amount = 850
    db.add(
        ReturnRefundAllocation(
            return_request_id=failed.id,
            order_id=order.id,
            order_item_id=items[0].id,
            component_kind="item",
            component_key=f"item:{items[0].id}",
            amount_cents=85000,
            policy_version=1,
        )
    )
    db.commit()

    current = _ret(db, order)
    options = refund_allocation_options(db, order=order)
    first_line = next(
        row for row in options["items"] if row["order_item_id"] == items[0].id
    )
    assert first_line["remaining_cents"] == 85000

    evidence = ensure_refund_allocation(
        db,
        order=order,
        ret=current,
        requested_amount=850,
        raw_allocations=[_spec("item", 850, item_id=items[0].id)],
    )
    assert evidence["item_cents"] == 85000


def test_goodwill_only_completed_refund_passes_without_fake_physical_mapping():
    db = _db()
    order, _items = _order(db)
    ret = _ret(db, order)
    ensure_refund_allocation(
        db,
        order=order,
        ret=ret,
        requested_amount=100,
        raw_allocations=[_spec("goodwill", 100)],
    )
    ret.refund_amount = 100
    ret.status = "approved_partial"
    db.commit()

    result = reconcile_refund_allocations(db, order.id)
    assert result["status"] == "PASS"
    assert result["completed_goodwill_cents"] == 10000
    assert result["completed_item_cents"] == 0
    assert result["physical_item_cents"] == 0


def _one_line_order(db, *, financial_amount=500):
    order, items = _order(
        db,
        total=10.0,
        delivery=0.0,
        discount=0.0,
        loyalty=0.0,
        items=((1, 5.0, 2),),
    )
    ret = _ret(db, order)
    ensure_refund_allocation(
        db,
        order=order,
        ret=ret,
        requested_amount=financial_amount / 100,
        raw_allocations=[
            _spec("item", financial_amount / 100, item_id=items[0].id, quantity=1),
        ],
    )
    ret.refund_amount = financial_amount / 100
    ret.status = "approved_partial"
    db.flush()

    case = ReturnLogisticsCase(
        return_request_id=ret.id,
        order_id=order.id,
        customer_id=order.customer_id,
        status="inspected",
        created_at=utcnow_naive(),
        updated_at=utcnow_naive(),
    )
    db.add(case)
    db.flush()
    physical = ReturnLogisticsItem(
        case_id=case.id,
        order_item_id=items[0].id,
        variant_id=items[0].variant_id,
        ordered_qty=2,
        authorized_qty=1,
        received_qty=1,
        inspected_qty=1,
        resalable_qty=1,
        damaged_qty=0,
        quarantine_qty=0,
    )
    db.add(physical)
    db.flush()
    db.add(
        ReturnLogisticsEvent(
            case_id=case.id,
            item_id=physical.id,
            event_type="inspected",
            disposition="resalable",
            quantity=1,
            idempotency_key=f"inspect-{case.id}",
            payload_hash="a" * 64,
            actor_admin_id=None,
            reason="test",
            created_at=utcnow_naive(),
        )
    )
    db.commit()
    return order, ret, items[0], case


def test_matching_completed_item_refund_and_inspected_physical_value_pass():
    db = _db()
    order, _ret_row, item, _case = _one_line_order(db, financial_amount=500)

    result = reconcile_refund_allocations(db, order.id)

    assert result["status"] == "PASS"
    assert result["lines"] == [
        {
            "order_item_id": item.id,
            "financial_cents": 500,
            "physical_cents": 500,
            "delta_cents": 0,
        }
    ]


def test_inspected_physical_financial_mismatch_requires_review_not_silent_green():
    db = _db()
    order, _ret_row, item, _case = _one_line_order(db, financial_amount=400)

    result = reconcile_refund_allocations(db, order.id)

    assert result["status"] == "REVIEW"
    assert "physical_value_exceeds_completed_item_refunds" in result["codes"]
    assert result["lines"][0]["order_item_id"] == item.id
    assert result["lines"][0]["delta_cents"] == -100


def test_financial_item_refund_without_physical_case_is_pending():
    db = _db()
    order, items = _order(db)
    ret = _ret(db, order)
    ensure_refund_allocation(
        db,
        order=order,
        ret=ret,
        requested_amount=100,
        raw_allocations=[_spec("item", 100, item_id=items[0].id)],
    )
    ret.refund_amount = 100
    ret.status = "approved_partial"
    db.commit()

    result = reconcile_refund_allocations(db, order.id)
    assert result["status"] == "PENDING"


def test_requested_return_without_allocation_is_pending_not_pass():
    db = _db()
    order, _items = _order(db)
    _ret(db, order, status="requested")
    db.commit()

    result = reconcile_refund_allocations(db, order.id)
    assert result["status"] == "PENDING"
