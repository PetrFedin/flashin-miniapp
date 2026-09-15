from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import (
    Customer,
    MoySkladConflict,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    ReturnRequest,
    StockReconciliationLog,
)
from backend.reverse_logistics_models import (
    ReturnLogisticsCase,
    ReturnLogisticsEvent,
    ReturnLogisticsItem,
)
from backend.services.moysklad import _apply_synced_stock
from backend.services.moysklad_stock_authority import evaluate_moysklad_stock_snapshot
from backend.services.stock_reconciliation import reconcile_stock_rows


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _variant(db, key: str, stock: int = 5, reserved: int = 0):
    customer = Customer(telegram_id=f"tg-{key}")
    product = Product(sku=key, title=key, slug=key, price=1000)
    db.add_all([customer, product])
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        sku=f"{key}-M",
        moysklad_id=f"ms-{key}",
        stock_qty=stock,
        reserved_qty=reserved,
    )
    db.add(variant)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="refunded",
        payment_status="refunded",
        total_amount=2000,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        variant_id=variant.id,
        title=key,
        size="M",
        quantity=2,
        price=1000,
    )
    db.add(item)
    db.commit()
    return variant, order, item


def _inspection(db, variant, order, item, disposition: str, quantity: int = 1):
    request = ReturnRequest(
        order_id=order.id,
        customer_id=order.customer_id,
        reason="physical evidence",
        status="approved",
        refund_amount=0,
    )
    db.add(request)
    db.flush()
    case = ReturnLogisticsCase(
        return_request_id=request.id,
        order_id=order.id,
        customer_id=order.customer_id,
        status="inspected",
    )
    db.add(case)
    db.flush()
    physical_item = ReturnLogisticsItem(
        case_id=case.id,
        order_item_id=item.id,
        variant_id=variant.id,
        ordered_qty=2,
        authorized_qty=quantity,
        received_qty=quantity,
        inspected_qty=quantity,
        resalable_qty=quantity if disposition == "resalable" else 0,
        damaged_qty=quantity if disposition == "damaged" else 0,
        quarantine_qty=quantity if disposition == "quarantine" else 0,
    )
    db.add(physical_item)
    db.flush()
    event = ReturnLogisticsEvent(
        case_id=case.id,
        item_id=physical_item.id,
        event_type="inspected",
        disposition=disposition,
        quantity=quantity,
        idempotency_key=f"inspect-{case.id}-{disposition}",
        payload_hash="b" * 64,
        reason="test",
    )
    db.add(event)
    db.commit()
    return event


def test_stale_provider_snapshot_cannot_erase_verified_resalable_stock():
    db = _db()
    variant, order, item = _variant(db, "protected", stock=5)
    event = _inspection(db, variant, order, item, "resalable")
    variant.stock_qty = 6
    db.commit()

    count = reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 5}], apply=True)
    db.refresh(variant)

    assert count == 1
    assert variant.stock_qty == 6
    conflict = db.query(MoySkladConflict).filter(MoySkladConflict.status == "open").one()
    assert conflict.conflict_type == "stale_stock_pending_physical_return"
    assert str(event.id) in conflict.message
    reconciliation = db.query(StockReconciliationLog).filter(
        StockReconciliationLog.action == "blocked_physical_return",
        StockReconciliationLog.status == "open",
    ).one()
    assert reconciliation.external_stock_qty == 5


def test_main_assortment_stock_writer_uses_same_authority_boundary():
    db = _db()
    variant, order, item = _variant(db, "main-sync", stock=5)
    _inspection(db, variant, order, item, "resalable")
    variant.stock_qty = 6
    db.commit()

    _apply_synced_stock(db, variant, 5, sync_type="manual", admin_id=None)
    db.commit()
    db.refresh(variant)

    assert variant.stock_qty == 6
    assert db.query(MoySkladConflict).filter(MoySkladConflict.status == "open").count() == 1


def test_damaged_or_quarantine_only_does_not_create_false_stock_protection():
    for disposition in ("damaged", "quarantine"):
        db = _db()
        variant, order, item = _variant(db, f"non-sellable-{disposition}", stock=6)
        _inspection(db, variant, order, item, disposition)

        reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 5}], apply=True)
        db.refresh(variant)

        assert variant.stock_qty == 5
        assert db.query(MoySkladConflict).filter(MoySkladConflict.status == "open").count() == 0


def test_mixed_physical_disposition_keeps_resalable_units_protected():
    db = _db()
    variant, order, item = _variant(db, "mixed", stock=5)
    _inspection(db, variant, order, item, "resalable")
    # A later damaged return does not revoke the earlier verified resalable unit.
    # Use a second original line because each physical case owns one order line.
    product = db.query(Product).filter(Product.id == variant.product_id).one()
    second_item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        variant_id=variant.id,
        title="mixed-2",
        size="M",
        quantity=2,
        price=1000,
    )
    db.add(second_item)
    db.commit()
    _inspection(db, variant, order, second_item, "damaged")
    variant.stock_qty = 6
    db.commit()

    decision = evaluate_moysklad_stock_snapshot(db, variant, 5)
    assert decision.blocked is True
    assert decision.target_stock == 6


def test_unrelated_variant_continues_sync_while_return_variant_is_protected():
    db = _db()
    protected, order, item = _variant(db, "protected-two", stock=5)
    other, _other_order, _other_item = _variant(db, "unrelated", stock=8)
    _inspection(db, protected, order, item, "resalable")
    protected.stock_qty = 6
    db.commit()

    reconcile_stock_rows(
        db,
        [
            {"sku": protected.sku, "stock_qty": 5},
            {"sku": other.sku, "stock_qty": 3},
        ],
        apply=True,
    )
    db.refresh(protected)
    db.refresh(other)

    assert protected.stock_qty == 6
    assert other.stock_qty == 3


def test_actual_provider_catchup_releases_guard_for_future_normal_sync():
    db = _db()
    variant, order, item = _variant(db, "catchup", stock=5)
    _inspection(db, variant, order, item, "resalable")
    variant.stock_qty = 6
    db.commit()

    reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 5}], apply=True)
    db.refresh(variant)
    assert variant.stock_qty == 6

    # The provider now actually exposes the warehouse-confirmed quantity.
    reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 6}], apply=True)
    catchup = db.query(StockReconciliationLog).filter(
        StockReconciliationLog.action == "physical_return_catchup",
        StockReconciliationLog.status == "resolved",
    ).one()
    assert catchup.external_stock_qty == 6
    assert db.query(MoySkladConflict).filter(MoySkladConflict.status == "open").count() == 0

    # Protection is not a permanent floor: a later legitimate provider change
    # is authoritative again after catch-up has actually been observed.
    reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 4}], apply=True)
    db.refresh(variant)
    assert variant.stock_qty == 4


def test_reserved_floor_cannot_fake_provider_catchup():
    db = _db()
    variant, order, item = _variant(db, "reserved-floor", stock=6, reserved=5)
    _inspection(db, variant, order, item, "resalable")

    decision = evaluate_moysklad_stock_snapshot(db, variant, 4)
    assert decision.blocked is True
    assert decision.target_stock == 6
    assert db.query(StockReconciliationLog).filter(
        StockReconciliationLog.action == "physical_return_catchup"
    ).count() == 0
