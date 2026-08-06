from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import (
    Customer,
    InventoryMovement,
    Order,
    Product,
    ProductVariant,
)
from backend.services.inventory import (
    commit_reservations_to_sold,
    release_variants,
    reserve_variant,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _order_and_variant(db, *, suffix: str):
    customer = Customer(telegram_id=f"ledger-{suffix}")
    product = Product(
        sku=f"LEDGER-PRODUCT-{suffix}",
        title=f"Ledger product {suffix}",
        slug=f"ledger-product-{suffix}",
        price=1000.0,
    )
    db.add_all([customer, product])
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"LEDGER-{suffix}",
        size="ONE",
        stock_qty=10,
        reserved_qty=0,
    )
    order = Order(
        customer_id=customer.id,
        status="created",
        payment_status="pending",
        total_amount=1000.0,
        currency="RUB",
    )
    db.add_all([variant, order])
    db.flush()
    return order, variant


def test_reserve_and_release_are_one_durable_order_linked_chain():
    db = _session()
    try:
        order, variant = _order_and_variant(db, suffix="release")
        reserve_variant(
            db,
            variant.id,
            2,
            order_id=order.id,
            source="checkout",
        )
        release_variants(
            db,
            {variant.id: 2},
            order_id=order.id,
            source="order_cancellation:manual",
        )
        db.flush()

        movements = (
            db.query(InventoryMovement)
            .filter(InventoryMovement.order_id == order.id)
            .order_by(InventoryMovement.id.asc())
            .all()
        )
        assert [movement.kind for movement in movements] == ["reserve", "release"]
        assert [movement.quantity for movement in movements] == [2, 2]
        assert (
            movements[0].stock_before,
            movements[0].stock_after,
            movements[0].reserved_before,
            movements[0].reserved_after,
        ) == (10, 10, 0, 2)
        assert (
            movements[1].stock_before,
            movements[1].stock_after,
            movements[1].reserved_before,
            movements[1].reserved_after,
        ) == (10, 10, 2, 0)
        assert movements[1].source == "order_cancellation:manual"
    finally:
        db.close()


def test_reserve_and_commit_capture_stock_and_reserved_snapshots():
    db = _session()
    try:
        order, variant = _order_and_variant(db, suffix="commit")
        reserve_variant(
            db,
            variant.id,
            3,
            order_id=order.id,
            source="checkout",
        )
        commit_reservations_to_sold(
            db,
            {variant.id: 3},
            order_id=order.id,
            source="payment_settlement",
        )
        db.flush()

        movements = (
            db.query(InventoryMovement)
            .filter(InventoryMovement.order_id == order.id)
            .order_by(InventoryMovement.id.asc())
            .all()
        )
        assert [movement.kind for movement in movements] == ["reserve", "commit"]
        assert (
            movements[1].stock_before,
            movements[1].stock_after,
            movements[1].reserved_before,
            movements[1].reserved_after,
        ) == (10, 7, 3, 0)
        assert variant.stock_qty == 7
        assert variant.reserved_qty == 0
    finally:
        db.close()


def test_production_inventory_callsites_are_order_attributed():
    root = Path(__file__).resolve().parents[2]
    checkout = (root / "backend/api/orders.py").read_text(encoding="utf-8")
    cancellation = (
        root / "backend/services/order_cancellation.py"
    ).read_text(encoding="utf-8")
    settlement = (
        root / "backend/services/payment_settlement.py"
    ).read_text(encoding="utf-8")

    assert "reserve_variant(" in checkout
    assert "order_id=order.id" in checkout
    assert 'source="checkout"' in checkout
    assert "release_variants(" in cancellation
    assert "order_id=order.id" in cancellation
    assert 'source=f"order_cancellation:{source}"' in cancellation
    assert "commit_reservations_to_sold(" in settlement
    assert "order_id=order.id" in settlement
    assert 'source="payment_settlement"' in settlement
