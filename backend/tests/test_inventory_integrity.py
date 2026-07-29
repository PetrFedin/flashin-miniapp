import json
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import (
    AuditLog,
    Customer,
    InventoryAdjustment,
    InventorySnapshot,
    Order,
    OrderItem,
    Product,
    ProductVariant,
)
from backend.services.inventory import (
    adjust_stock,
    commit_reserved_to_sold,
    release_variant,
    reserve_variant,
    restock_inventory,
    snapshot_inventory,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _variant(db, *, stock=10, reserved=0):
    sequence = db.query(Product).count() + 1
    product = Product(
        sku=f"INV-PRODUCT-{sequence}",
        title="Inventory product",
        slug=f"inventory-product-{sequence}",
        brand="FLASHIN",
        price=100.0,
        currency="RUB",
        category="Clothing",
        gender="unisex",
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="black",
        sku=f"INV-VARIANT-{sequence}",
        stock_qty=stock,
        reserved_qty=reserved,
    )
    db.add(variant)
    db.commit()
    return product, variant


def _order_item(db, product, variant, *, quantity, created_at, order_status, payment_status):
    customer = Customer(telegram_id=f"inventory-customer-{db.query(Customer).count() + 1}")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status=order_status,
        payment_status=payment_status,
        total_amount=quantity * 100.0,
        currency="RUB",
        created_at=created_at,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            title=product.title,
            size=variant.size,
            quantity=quantity,
            price=100.0,
        )
    )
    db.commit()
    return order


def _movement_rows(db):
    return db.query(AuditLog).filter(AuditLog.action.like("inventory.%")).order_by(AuditLog.id).all()


def test_reserve_then_sale_updates_single_source_of_truth_and_audit_log():
    db = _session()
    _product, variant = _variant(db, stock=10)
    reserve_variant(db, variant.id, 2)
    db.commit()
    db.refresh(variant)
    assert (variant.stock_qty, variant.reserved_qty) == (10, 2)

    commit_reserved_to_sold(db, variant.id, 2)
    db.commit()
    db.refresh(variant)
    assert (variant.stock_qty, variant.reserved_qty) == (8, 0)

    movements = _movement_rows(db)
    assert [row.action for row in movements] == ["inventory.reserve", "inventory.sale"]
    reserve_payload = json.loads(movements[0].payload)
    sale_payload = json.loads(movements[1].payload)
    assert reserve_payload == {
        "reason": "checkout reservation",
        "reserved_after": 2,
        "reserved_before": 0,
        "reserved_delta": 2,
        "sku": variant.sku,
        "stock_after": 10,
        "stock_before": 10,
        "stock_delta": 0,
    }
    assert sale_payload["reserved_delta"] == -2
    assert sale_payload["stock_delta"] == -2
    assert sale_payload["stock_after"] == 8


def test_release_records_exact_reservation_delta_without_changing_stock():
    db = _session()
    _product, variant = _variant(db, stock=10, reserved=4)
    release_variant(db, variant.id, 3)
    db.commit()
    db.refresh(variant)

    assert (variant.stock_qty, variant.reserved_qty) == (10, 1)
    movement = _movement_rows(db)[0]
    payload = json.loads(movement.payload)
    assert movement.action == "inventory.release"
    assert payload["reserved_delta"] == -3
    assert payload["stock_delta"] == 0


@pytest.mark.parametrize("operation", [release_variant, commit_reserved_to_sold])
def test_movement_above_reserved_quantity_fails_without_mutation_or_audit(operation):
    db = _session()
    _product, variant = _variant(db, stock=10, reserved=2)
    with pytest.raises(HTTPException) as caught:
        operation(db, variant.id, 3)
    assert caught.value.status_code == 409
    db.refresh(variant)
    assert (variant.stock_qty, variant.reserved_qty) == (10, 2)
    assert _movement_rows(db) == []


def test_manual_adjustment_cannot_hide_reservation_shortage():
    db = _session()
    _product, variant = _variant(db, stock=10, reserved=4)
    with pytest.raises(HTTPException) as caught:
        adjust_stock(db, variant.id, 3, "cycle count")
    assert caught.value.status_code == 409
    assert "reserved quantity" in str(caught.value.detail)
    db.refresh(variant)
    assert (variant.stock_qty, variant.reserved_qty) == (10, 4)
    assert db.query(InventoryAdjustment).count() == 0
    assert _movement_rows(db) == []


def test_valid_manual_adjustment_records_adjustment_and_exact_audit_delta():
    db = _session()
    _product, variant = _variant(db, stock=10, reserved=4)
    adjust_stock(db, variant.id, 7, "cycle count")
    db.commit()

    db.refresh(variant)
    adjustment = db.query(InventoryAdjustment).one()
    movement = _movement_rows(db)[0]
    payload = json.loads(movement.payload)
    assert (variant.stock_qty, variant.reserved_qty) == (7, 4)
    assert (adjustment.old_stock_qty, adjustment.new_stock_qty) == (10, 7)
    assert adjustment.reason == "cycle count"
    assert movement.action == "inventory.adjust"
    assert payload["stock_delta"] == -3
    assert payload["reserved_delta"] == 0


def test_noop_manual_adjustment_is_audited_as_an_explicit_decision():
    db = _session()
    _product, variant = _variant(db, stock=10, reserved=4)
    adjust_stock(db, variant.id, 10, "verified count")
    db.commit()
    adjustment = db.query(InventoryAdjustment).one()
    movement = _movement_rows(db)[0]
    assert adjustment.old_stock_qty == adjustment.new_stock_qty == 10
    assert json.loads(movement.payload)["stock_delta"] == 0


def test_restock_counts_only_paid_sales_and_uses_available_stock():
    db = _session()
    product, variant = _variant(db, stock=10, reserved=4)
    period_start = datetime(2026, 1, 1, 0, 0, 0)
    period_end = datetime(2026, 1, 2, 23, 59, 59)
    _order_item(db, product, variant, quantity=4, created_at=datetime(2026, 1, 1, 12), order_status="paid", payment_status="paid")
    _order_item(db, product, variant, quantity=2, created_at=datetime(2026, 1, 2, 12), order_status="completed", payment_status="partially_refunded")
    _order_item(db, product, variant, quantity=100, created_at=datetime(2026, 1, 1, 13), order_status="created", payment_status="pending")
    _order_item(db, product, variant, quantity=100, created_at=datetime(2026, 1, 1, 14), order_status="cancelled", payment_status="paid")
    _order_item(db, product, variant, quantity=100, created_at=datetime(2026, 1, 2, 14), order_status="refunded", payment_status="paid")

    row = restock_inventory(db, period_start, period_end, lead_time_days=2, safety_stock_days=1)[0]
    assert row["sold_count"] == 6
    assert row["period_days"] == 2
    assert row["avg_daily_sales"] == 3
    assert row["lead_time_demand"] == 6
    assert row["safety_stock"] == 3
    assert row["target_stock"] == 9
    assert row["current_stock"] == 10
    assert row["reserved_stock"] == 4
    assert row["available_stock"] == 6
    assert row["restock_qty"] == 3


def test_one_calendar_day_is_not_divided_by_zero_or_shortened():
    db = _session()
    product, variant = _variant(db, stock=0)
    day_start = datetime(2026, 2, 1, 0, 0, 0)
    day_end = datetime(2026, 2, 1, 23, 59, 59)
    _order_item(db, product, variant, quantity=5, created_at=datetime(2026, 2, 1, 12), order_status="paid", payment_status="paid")
    row = restock_inventory(db, day_start, day_end, lead_time_days=1, safety_stock_days=0)[0]
    assert row["period_days"] == 1
    assert row["avg_daily_sales"] == 5
    assert row["restock_qty"] == 5


def test_restock_rounds_fractional_demand_up_instead_of_underordering():
    db = _session()
    product, variant = _variant(db, stock=0)
    start = datetime(2026, 4, 1)
    end = datetime(2026, 4, 3, 23, 59, 59)
    _order_item(db, product, variant, quantity=1, created_at=datetime(2026, 4, 2, 12), order_status="paid", payment_status="paid")
    row = restock_inventory(db, start, end, lead_time_days=2, safety_stock_days=1)[0]
    assert row["avg_daily_sales"] == pytest.approx(1 / 3)
    assert row["lead_time_demand"] == 1
    assert row["safety_stock"] == 1
    assert row["restock_qty"] == 2


def test_restock_uses_constant_number_of_selects_when_sku_count_grows():
    db = _session()
    for _ in range(20):
        _variant(db, stock=10)
    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(db.bind, "before_cursor_execute", count_selects)
    try:
        rows = restock_inventory(db, datetime(2026, 1, 1), datetime(2026, 1, 31, 23, 59, 59))
    finally:
        event.remove(db.bind, "before_cursor_execute", count_selects)
    assert len(rows) == 20
    assert select_count == 2


@pytest.mark.parametrize(
    ("operation", "quantity"),
    [
        (reserve_variant, True),
        (reserve_variant, 1.5),
        (reserve_variant, 1_000_001),
        (release_variant, 0),
        (commit_reserved_to_sold, -1),
    ],
)
def test_inventory_movements_reject_invalid_quantities(operation, quantity):
    db = _session()
    _product, variant = _variant(db, stock=10, reserved=5)
    with pytest.raises(HTTPException) as caught:
        operation(db, variant.id, quantity)
    assert caught.value.status_code == 400
    assert _movement_rows(db) == []


@pytest.mark.parametrize("variant_id", [True, 0, -1, "1"])
def test_inventory_movements_reject_invalid_variant_ids(variant_id):
    db = _session()
    with pytest.raises(HTTPException) as caught:
        reserve_variant(db, variant_id, 1)
    assert caught.value.status_code == 400


def test_restock_rejects_invalid_period_and_planning_inputs():
    db = _session()
    _variant(db)
    start = datetime(2026, 3, 2)
    end = datetime(2026, 3, 1)
    with pytest.raises(HTTPException) as period_error:
        restock_inventory(db, start, end)
    assert period_error.value.status_code == 400
    with pytest.raises(HTTPException) as lead_error:
        restock_inventory(db, end, start, lead_time_days=0)
    assert lead_error.value.status_code == 400
    with pytest.raises(HTTPException) as safety_error:
        restock_inventory(db, end, start, safety_stock_days=True)
    assert safety_error.value.status_code == 400
    with pytest.raises(HTTPException) as horizon_error:
        restock_inventory(db, end, start, lead_time_days=3_651)
    assert horizon_error.value.status_code == 400


def test_inventory_reason_is_required_bounded_and_normalized():
    db = _session()
    _product, variant = _variant(db)
    with pytest.raises(HTTPException) as short:
        adjust_stock(db, variant.id, 9, "x")
    assert short.value.status_code == 400
    with pytest.raises(HTTPException) as long:
        adjust_stock(db, variant.id, 9, "x" * 256)
    assert long.value.status_code == 400
    with pytest.raises(HTTPException) as invalid:
        adjust_stock(db, variant.id, 9, "bad\x00reason")
    assert invalid.value.status_code == 400
    adjust_stock(db, variant.id, 9, "  cycle count  ")
    db.commit()
    assert db.query(InventoryAdjustment).one().reason == "cycle count"


def test_snapshot_validates_source_and_captures_exact_variant_state():
    db = _session()
    _variant(db, stock=10, reserved=3)
    _variant(db, stock=4, reserved=0)
    assert snapshot_inventory(db, "nightly") == 2
    db.commit()
    snapshots = db.query(InventorySnapshot).order_by(InventorySnapshot.variant_id).all()
    assert [(row.stock_qty, row.reserved_qty, row.source) for row in snapshots] == [
        (10, 3, "nightly"),
        (4, 0, "nightly"),
    ]
    with pytest.raises(HTTPException) as invalid:
        snapshot_inventory(db, "x" * 65)
    assert invalid.value.status_code == 400
