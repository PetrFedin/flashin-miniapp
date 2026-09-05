from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import (
    Customer,
    InventoryMovement,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    StockReconciliationLog,
)
from backend.services.inventory import commit_reserved_to_sold, reserve_variant
from backend.services.pilot_inventory_safety import (
    _movement_transition_valid,
    build_pilot_inventory_safety,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _order_fixture(db, *, status: str = "pending", quantity: int = 1, sku: str = "sku-1"):
    customer = Customer(telegram_id=f"tg-{sku}")
    product = Product(sku=sku, title=f"Product {sku}", slug=sku, price=1000)
    db.add_all([customer, product])
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        sku=f"{sku}-M",
        stock_qty=10,
        reserved_qty=0,
    )
    db.add(variant)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status=status,
        payment_status="pending",
        total_amount=1000 * quantity,
        currency="RUB",
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
            price=1000,
        )
    )
    db.commit()
    return order, variant


def _reconciliation(variant: ProductVariant, *, external_stock_qty: int, status: str):
    return StockReconciliationLog(
        variant_id=variant.id,
        sku=variant.sku,
        local_stock_qty=variant.stock_qty,
        external_stock_qty=external_stock_qty,
        local_reserved_qty=variant.reserved_qty,
        action="report",
        status=status,
    )


def test_empty_pilot_is_healthy_and_identifier_free():
    db = _db()
    verdict = build_pilot_inventory_safety(db, [])
    assert verdict == {
        "healthy": True,
        "blocking_codes": [],
        "pilot_orders": 0,
        "pilot_variants": 0,
        "open_reconciliation_variants": 0,
        "chain_failures": 0,
        "stop_reason": None,
    }


def test_pending_order_requires_exact_reserve_chain():
    db = _db()
    order, variant = _order_fixture(db)

    missing = build_pilot_inventory_safety(db, [order.id])
    assert missing["healthy"] is False
    assert "inventory_movement_chain_invalid" in missing["blocking_codes"]

    reserve_variant(db, variant.id, 1, order_id=order.id, source="checkout")
    db.commit()

    healthy = build_pilot_inventory_safety(db, [order.id])
    assert healthy["healthy"] is True
    assert healthy["pilot_orders"] == 1
    assert healthy["pilot_variants"] == 1
    assert healthy["chain_failures"] == 0


def test_paid_order_requires_commit_and_respects_order_item_quantity():
    db = _db()
    order, variant = _order_fixture(db, status="paid", quantity=2, sku="paid")
    reserve_variant(db, variant.id, 2, order_id=order.id, source="checkout")
    db.commit()

    before_commit = build_pilot_inventory_safety(db, [order.id])
    assert before_commit["healthy"] is False
    assert "inventory_movement_chain_invalid" in before_commit["blocking_codes"]

    commit_reserved_to_sold(db, variant.id, 2, order_id=order.id, source="payment")
    db.commit()

    after_commit = build_pilot_inventory_safety(db, [order.id])
    assert after_commit["healthy"] is True


def test_transition_conservation_rejects_bad_snapshot_and_accepts_return():
    bad = InventoryMovement(
        order_id=1,
        variant_id=1,
        kind="reserve",
        quantity=1,
        stock_before=10,
        stock_after=9,
        reserved_before=0,
        reserved_after=1,
        source="test",
    )
    assert _movement_transition_valid(bad) is False

    returned = InventoryMovement(
        order_id=1,
        variant_id=1,
        kind="return",
        quantity=2,
        stock_before=8,
        stock_after=10,
        reserved_before=0,
        reserved_after=0,
        source="refund",
    )
    assert _movement_transition_valid(returned) is True


def test_latest_open_reconciliation_blocks_but_newer_resolved_clears_it():
    db = _db()
    order, variant = _order_fixture(db, sku="reconcile")
    reserve_variant(db, variant.id, 1, order_id=order.id, source="checkout")
    db.add(_reconciliation(variant, external_stock_qty=7, status="open"))
    db.commit()

    open_verdict = build_pilot_inventory_safety(db, [order.id])
    assert open_verdict["healthy"] is False
    assert open_verdict["open_reconciliation_variants"] == 1
    assert "inventory_reconciliation_open" in open_verdict["blocking_codes"]

    db.add(_reconciliation(variant, external_stock_qty=10, status="resolved"))
    db.commit()

    resolved_verdict = build_pilot_inventory_safety(db, [order.id])
    assert resolved_verdict["healthy"] is True
    assert resolved_verdict["open_reconciliation_variants"] == 0


def test_unrelated_variant_reconciliation_does_not_block_pilot_order():
    db = _db()
    order, variant = _order_fixture(db, sku="pilot")
    reserve_variant(db, variant.id, 1, order_id=order.id, source="checkout")
    _other_order, other_variant = _order_fixture(db, sku="other")
    db.add(_reconciliation(other_variant, external_stock_qty=1, status="open"))
    db.commit()

    verdict = build_pilot_inventory_safety(db, [order.id])
    assert verdict["healthy"] is True
    assert verdict["open_reconciliation_variants"] == 0
