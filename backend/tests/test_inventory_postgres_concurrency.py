from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.database import SessionLocal, engine
from backend.models import (
    Customer,
    InventoryAdjustment,
    InventoryMovement,
    Order,
    Product,
    ProductVariant,
)
from backend.services.inventory import adjust_stock, reserve_variant


pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="Requires PostgreSQL row-level locking semantics",
)


def _seed_last_unit() -> dict[str, object]:
    token = uuid4().hex[:16]
    db = SessionLocal()
    try:
        customers = [
            Customer(telegram_id=f"inventory-race-{token}-1"),
            Customer(telegram_id=f"inventory-race-{token}-2"),
        ]
        db.add_all(customers)
        db.flush()

        product = Product(
            sku=f"RACE-{token}",
            title="Inventory race fixture",
            slug=f"inventory-race-{token}",
            brand="FLASHIN",
            description="PostgreSQL row-lock regression fixture",
            price=100.0,
            currency="RUB",
            category="Test",
            gender="unisex",
            active=False,
        )
        db.add(product)
        db.flush()

        variant = ProductVariant(
            product_id=product.id,
            size="OS",
            color="",
            sku=f"RACE-{token}-OS",
            stock_qty=1,
            reserved_qty=0,
        )
        db.add(variant)
        db.flush()

        orders = [
            Order(customer_id=customers[0].id, total_amount=100.0),
            Order(customer_id=customers[1].id, total_amount=100.0),
        ]
        db.add_all(orders)
        db.flush()

        fixture = {
            "customer_ids": [customer.id for customer in customers],
            "product_id": product.id,
            "variant_id": variant.id,
            "order_ids": [order.id for order in orders],
        }
        db.commit()
        return fixture
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _cleanup_fixture(fixture: dict[str, object]) -> None:
    db = SessionLocal()
    try:
        variant_id = int(fixture["variant_id"])
        order_ids = list(fixture["order_ids"])
        customer_ids = list(fixture["customer_ids"])
        db.query(InventoryMovement).filter(
            InventoryMovement.order_id.in_(order_ids)
        ).delete(synchronize_session=False)
        db.query(InventoryAdjustment).filter(
            InventoryAdjustment.variant_id == variant_id
        ).delete(synchronize_session=False)
        db.query(Order).filter(Order.id.in_(order_ids)).delete(synchronize_session=False)
        db.query(ProductVariant).filter(ProductVariant.id == variant_id).delete(
            synchronize_session=False
        )
        db.query(Product).filter(Product.id == int(fixture["product_id"])).delete(
            synchronize_session=False
        )
        db.query(Customer).filter(Customer.id.in_(customer_ids)).delete(
            synchronize_session=False
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _reserve_once(variant_id: int, order_id: int, barrier: Barrier) -> str:
    db = SessionLocal()
    try:
        barrier.wait(timeout=10)
        reserve_variant(
            db,
            variant_id,
            1,
            order_id=order_id,
            source="postgres_concurrency_test",
        )
        db.commit()
        return "ok"
    except HTTPException as exc:
        db.rollback()
        return f"http_{exc.status_code}"
    finally:
        db.close()


def _adjust_to_zero(variant_id: int, barrier: Barrier) -> str:
    db = SessionLocal()
    try:
        barrier.wait(timeout=10)
        adjust_stock(
            db,
            variant_id,
            0,
            reason="postgres_concurrency_test",
        )
        db.commit()
        return "ok"
    except HTTPException as exc:
        db.rollback()
        return f"http_{exc.status_code}"
    finally:
        db.close()


def test_two_customers_cannot_reserve_the_same_final_unit():
    fixture = _seed_last_unit()
    try:
        variant_id = int(fixture["variant_id"])
        order_ids = list(fixture["order_ids"])
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(_reserve_once, variant_id, int(order_id), barrier)
                for order_id in order_ids
            ]
            outcomes = sorted(future.result(timeout=20) for future in futures)

        assert outcomes == ["http_409", "ok"]

        db = SessionLocal()
        try:
            variant = db.get(ProductVariant, variant_id)
            assert variant is not None
            assert variant.stock_qty == 1
            assert variant.reserved_qty == 1
            assert 0 <= variant.reserved_qty <= variant.stock_qty
            movements = (
                db.query(InventoryMovement)
                .filter(
                    InventoryMovement.variant_id == variant_id,
                    InventoryMovement.order_id.in_(order_ids),
                    InventoryMovement.kind == "reserve",
                )
                .all()
            )
            assert len(movements) == 1
            assert movements[0].quantity == 1
        finally:
            db.close()
    finally:
        _cleanup_fixture(fixture)


def test_warehouse_stock_edit_and_checkout_preserve_inventory_invariant():
    fixture = _seed_last_unit()
    try:
        variant_id = int(fixture["variant_id"])
        order_id = int(list(fixture["order_ids"])[0])
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            reserve_future = executor.submit(_reserve_once, variant_id, order_id, barrier)
            adjust_future = executor.submit(_adjust_to_zero, variant_id, barrier)
            reserve_outcome = reserve_future.result(timeout=20)
            adjust_outcome = adjust_future.result(timeout=20)

        assert sorted([reserve_outcome, adjust_outcome]) == ["http_409", "ok"]

        db = SessionLocal()
        try:
            variant = db.get(ProductVariant, variant_id)
            assert variant is not None
            assert 0 <= variant.reserved_qty <= variant.stock_qty

            reserve_movements = (
                db.query(InventoryMovement)
                .filter(
                    InventoryMovement.variant_id == variant_id,
                    InventoryMovement.order_id == order_id,
                    InventoryMovement.kind == "reserve",
                )
                .count()
            )
            adjustments = (
                db.query(InventoryAdjustment)
                .filter(
                    InventoryAdjustment.variant_id == variant_id,
                    InventoryAdjustment.reason == "postgres_concurrency_test",
                )
                .count()
            )

            if reserve_outcome == "ok":
                assert adjust_outcome == "http_409"
                assert (variant.stock_qty, variant.reserved_qty) == (1, 1)
                assert reserve_movements == 1
                assert adjustments == 0
            else:
                assert reserve_outcome == "http_409"
                assert adjust_outcome == "ok"
                assert (variant.stock_qty, variant.reserved_qty) == (0, 0)
                assert reserve_movements == 0
                assert adjustments == 1
        finally:
            db.close()
    finally:
        _cleanup_fixture(fixture)
