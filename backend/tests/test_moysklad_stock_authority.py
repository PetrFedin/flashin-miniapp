from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import (
    Customer,
    InventoryMovement,
    MoySkladConflict,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    ReturnRequest,
    StockReconciliationLog,
)
from backend.provider_models import ProviderCommand
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
    db.flush()
    if disposition == "resalable":
        before = int(variant.stock_qty)
        variant.stock_qty = before + quantity
        db.add(
            InventoryMovement(
                order_id=order.id,
                variant_id=variant.id,
                kind="return",
                quantity=quantity,
                stock_before=before,
                stock_after=before + quantity,
                reserved_before=int(variant.reserved_qty),
                reserved_after=int(variant.reserved_qty),
                source=f"reverse_logistics_event:{event.id}",
            )
        )
    db.commit()
    return event, case


def _provider_command(db, case, *, status: str, external_id: str = ""):
    command = ProviderCommand(
        provider="moysklad",
        command_type="moysklad.physical_sales_return.create",
        idempotency_key=f"physical-return:{case.id}:sales_return:v1",
        aggregate_type="return_logistics_case",
        aggregate_id=str(case.id),
        payload_json=f'{{"case_id":{case.id}}}',
        status=status,
        attempts=1,
        external_id=external_id,
    )
    db.add(command)
    db.commit()
    return command


def test_stale_provider_snapshot_cannot_erase_verified_resalable_stock():
    db = _db()
    variant, order, item = _variant(db, "protected", stock=5)
    event, _case = _inspection(db, variant, order, item, "resalable")

    count = reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 5}], apply=True)
    db.refresh(variant)

    assert count == 1
    assert variant.stock_qty == 6
    conflict = db.query(MoySkladConflict).filter(MoySkladConflict.status == "open").one()
    assert conflict.conflict_type == "stale_stock_pending_physical_return"
    assert str(event.id) in conflict.message
    assert db.query(StockReconciliationLog).filter(
        StockReconciliationLog.action == "blocked_physical_return",
        StockReconciliationLog.status == "open",
    ).count() == 1


def test_main_assortment_stock_writer_uses_same_authority_boundary():
    db = _db()
    variant, order, item = _variant(db, "main-sync", stock=5)
    _inspection(db, variant, order, item, "resalable")

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


def test_review_required_resalable_case_cannot_clear_guard_even_on_high_snapshot():
    db = _db()
    variant, order, item = _variant(db, "mixed-review", stock=5)
    _event, case = _inspection(db, variant, order, item, "resalable")
    _provider_command(db, case, status="review_required")

    decision = evaluate_moysklad_stock_snapshot(db, variant, 10)

    assert decision.blocked is True
    assert decision.target_stock == 6
    assert db.query(MoySkladConflict).filter(MoySkladConflict.status == "open").count() == 1


def test_unrelated_variant_continues_sync_while_return_variant_is_protected():
    db = _db()
    protected, order, item = _variant(db, "protected-two", stock=5)
    other, _other_order, _other_item = _variant(db, "unrelated", stock=8)
    _inspection(db, protected, order, item, "resalable")

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


def test_sent_command_alone_is_not_catchup_and_old_snapshot_remains_blocked():
    db = _db()
    variant, order, item = _variant(db, "sent-not-caught", stock=5)
    _event, case = _inspection(db, variant, order, item, "resalable")
    _provider_command(db, case, status="sent", external_id="sales-return-1")

    decision = evaluate_moysklad_stock_snapshot(db, variant, 5)

    assert decision.blocked is True
    assert decision.target_stock == 6


def test_local_sale_after_return_cannot_fake_provider_catchup():
    db = _db()
    variant, order, item = _variant(db, "local-sale", stock=5)
    _event, case = _inspection(db, variant, order, item, "resalable")
    _provider_command(db, case, status="sent", external_id="sales-return-2")
    # Return ledger says stock_after=6. A later local sale lowers current stock,
    # so an old provider value of 5 must not be mistaken for catch-up.
    variant.stock_qty = 5
    db.commit()

    decision = evaluate_moysklad_stock_snapshot(db, variant, 5)

    assert decision.blocked is True
    assert db.query(StockReconciliationLog).filter(
        StockReconciliationLog.action == "physical_return_catchup"
    ).count() == 0


def test_actual_provider_transaction_and_stock_catchup_release_guard():
    db = _db()
    variant, order, item = _variant(db, "catchup", stock=5)
    _event, case = _inspection(db, variant, order, item, "resalable")

    reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 5}], apply=True)
    db.refresh(variant)
    assert variant.stock_qty == 6

    _provider_command(db, case, status="sent", external_id="sales-return-3")
    reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 6}], apply=True)

    catchup = db.query(StockReconciliationLog).filter(
        StockReconciliationLog.action == "physical_return_catchup",
        StockReconciliationLog.status == "resolved",
    ).one()
    assert catchup.external_stock_qty == 6
    assert db.query(MoySkladConflict).filter(MoySkladConflict.status == "open").count() == 0

    reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 4}], apply=True)
    db.refresh(variant)
    assert variant.stock_qty == 4


def test_reserved_floor_cannot_fake_provider_catchup():
    db = _db()
    variant, order, item = _variant(db, "reserved-floor", stock=6, reserved=5)
    _event, case = _inspection(db, variant, order, item, "resalable")
    _provider_command(db, case, status="sent", external_id="sales-return-4")

    decision = evaluate_moysklad_stock_snapshot(db, variant, 4)

    assert decision.blocked is True
    assert decision.target_stock == 7
    assert db.query(StockReconciliationLog).filter(
        StockReconciliationLog.action == "physical_return_catchup"
    ).count() == 0


def test_blocked_operational_evidence_survives_business_transaction_rollback(tmp_path):
    """Conflict evidence must outlive the caller transaction that was rejected."""

    database_path = tmp_path / "moysklad-authority-rollback.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)

    setup = SessionFactory()
    variant, order, item = _variant(setup, "rollback-evidence", stock=5)
    event, _case = _inspection(setup, variant, order, item, "resalable")
    variant_id = int(variant.id)
    event_id = int(event.id)
    setup.close()

    business = SessionFactory()
    try:
        current_variant = business.get(ProductVariant, variant_id)
        assert current_variant is not None
        decision = evaluate_moysklad_stock_snapshot(business, current_variant, 5)
        assert decision.blocked is True
        assert decision.pending_event_ids == (event_id,)

        # Prove the caller really rolls back mutable business state after the
        # authority decision. The operational evidence must not roll back with it.
        current_variant.reserved_qty = 2
        business.flush()
        business.rollback()
    finally:
        business.close()

    verify = SessionFactory()
    try:
        persisted_variant = verify.get(ProductVariant, variant_id)
        assert persisted_variant is not None
        assert persisted_variant.stock_qty == 6
        assert persisted_variant.reserved_qty == 0
        conflict = verify.query(MoySkladConflict).filter(
            MoySkladConflict.status == "open",
            MoySkladConflict.conflict_type == "stale_stock_pending_physical_return",
        ).one()
        assert str(event_id) in conflict.message
        blocked = verify.query(StockReconciliationLog).filter(
            StockReconciliationLog.variant_id == variant_id,
            StockReconciliationLog.action == "blocked_physical_return",
            StockReconciliationLog.status == "open",
        ).one()
        assert blocked.external_stock_qty == 5
        assert blocked.local_stock_qty == 6
    finally:
        verify.close()
        engine.dispose()
