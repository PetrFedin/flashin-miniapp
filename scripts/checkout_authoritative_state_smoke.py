#!/usr/bin/env python3
"""Prove checkout decisions use fresh authoritative PostgreSQL state.

The smoke uses the real checkout handler and pricing/inventory services. The
2-buyer case injects a barrier after each checkout has loaded its cart so both
SQLAlchemy Sessions hold the formerly-dangerous cached Product/Variant objects
before authoritative locks are acquired. The 10- and 20-buyer cases add
contention coverage without requiring more simultaneous DB connections than the
application pool. No payment/provider call is made.
"""

from __future__ import annotations

import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.api import orders as orders_api
from backend.database import SessionLocal, engine
from backend.delivery_schemas import DeliveryCheckoutIn
from backend.models import (
    Cart,
    CartItem,
    Customer,
    InventoryMovement,
    Order,
    Product,
    ProductVariant,
)
from backend.services.pricing import load_product_price_quotes


def _product_fixture(token: str, suffix: str) -> tuple[int, int]:
    with SessionLocal() as db:
        product = Product(
            sku=f"AUTH-{token}-{suffix}",
            slug=f"authoritative-{token}-{suffix}",
            title=f"Authoritative {suffix}",
            price=Decimal("100.00"),
            currency="RUB",
            active=True,
        )
        variant = ProductVariant(
            product=product,
            size="M",
            color="Black",
            sku=f"AUTH-V-{token}-{suffix}",
            stock_qty=1,
            reserved_qty=0,
        )
        db.add_all([product, variant])
        db.commit()
        return int(product.id), int(variant.id)


def _last_unit_race(token: str, buyer_count: int, *, synchronize_preload: bool) -> dict:
    if buyer_count < 2:
        raise ValueError("buyer_count must be at least 2")

    race_token = f"{token}-{buyer_count}"
    product_id, variant_id = _product_fixture(race_token, "race")
    customer_ids: list[int] = []
    with SessionLocal() as db:
        for number in range(buyer_count):
            customer = Customer(
                telegram_id=f"authoritative-{race_token}-{number}",
                first_name="Authoritative",
            )
            db.add(customer)
            db.flush()
            cart = Cart(customer_id=customer.id, status="active")
            db.add(cart)
            db.flush()
            db.add(
                CartItem(
                    cart_id=cart.id,
                    product_id=product_id,
                    variant_id=variant_id,
                    quantity=1,
                )
            )
            customer_ids.append(int(customer.id))
        db.commit()

    # This barrier is reached before the first query, so even the 20-buyer case
    # does not need 20 checked-out connections from SQLAlchemy's default pool.
    start_barrier = threading.Barrier(buyer_count, timeout=20)
    preload_barrier = threading.Barrier(buyer_count, timeout=20) if synchronize_preload else None
    original_loader = orders_api._load_locked_active_cart

    def synchronized_loader(db: Session, customer_id: int):
        cart = original_loader(db, customer_id)
        if preload_barrier is not None:
            preload_barrier.wait()
        return cart

    def checkout_customer(customer_id: int) -> dict:
        with SessionLocal() as db:
            start_barrier.wait()
            customer = db.query(Customer).filter(Customer.id == customer_id).one()
            payload = DeliveryCheckoutIn(
                name="Authoritative Buyer",
                phone="+79990000001",
                delivery_type="pickup",
                address="",
                comment=f"authoritative-state-smoke-{buyer_count}",
            )
            try:
                order = orders_api.checkout(
                    payload=payload,
                    idempotency_key_header=f"auth-{uuid.uuid4().hex}",
                    customer=customer,
                    db=db,
                )
                return {"outcome": "accepted", "order_id": int(order.id)}
            except HTTPException as exc:
                db.rollback()
                return {
                    "outcome": "rejected",
                    "status_code": int(exc.status_code),
                    "detail": str(exc.detail),
                }

    loader = synchronized_loader if synchronize_preload else original_loader
    with patch.object(orders_api, "_load_locked_active_cart", loader):
        with ThreadPoolExecutor(max_workers=buyer_count) as pool:
            outcomes = list(pool.map(checkout_customer, customer_ids))

    with SessionLocal() as db:
        variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).one()
        accepted = [row for row in outcomes if row["outcome"] == "accepted"]
        rejected = [row for row in outcomes if row["outcome"] == "rejected"]
        reserve_movements = (
            db.query(InventoryMovement)
            .filter(
                InventoryMovement.variant_id == variant_id,
                InventoryMovement.kind == "reserve",
            )
            .all()
        )
        accepted_orders = (
            db.query(Order)
            .filter(Order.id.in_([row["order_id"] for row in accepted]))
            .count()
            if accepted
            else 0
        )
        ledger_quantity = sum(int(row.quantity) for row in reserve_movements)
        assert len(accepted) == 1, outcomes
        assert len(rejected) == buyer_count - 1, outcomes
        assert all(row["status_code"] == 409 for row in rejected), outcomes
        assert accepted_orders == 1
        assert int(variant.stock_qty) == 1
        assert int(variant.reserved_qty) == 1
        assert ledger_quantity == 1
        return {
            "buyers": buyer_count,
            "accepted": len(accepted),
            "rejected": len(rejected),
            "stock_qty": int(variant.stock_qty),
            "reserved_qty": int(variant.reserved_qty),
            "reserve_ledger_quantity": ledger_quantity,
            "forced_stale_preload": synchronize_preload,
        }


def _price_refresh(token: str) -> dict:
    product_id, _ = _product_fixture(token, "price")
    with SessionLocal() as reader:
        cached = reader.query(Product).filter(Product.id == product_id).one()
        assert Decimal(str(cached.price)).quantize(Decimal("0.01")) == Decimal("100.00")
        with SessionLocal() as writer:
            current = writer.query(Product).filter(Product.id == product_id).one()
            current.price = Decimal("200.00")
            writer.commit()
        quote = load_product_price_quotes(reader, [cached], lock=True)[product_id]
        assert quote.effective_price == Decimal("200.00"), quote
        assert Decimal(str(cached.price)).quantize(Decimal("0.01")) == Decimal("200.00")
        return {
            "committed_price": "200.00",
            "locked_quote_price": format(quote.effective_price, ".2f"),
        }


def _deactivation_refresh(token: str) -> dict:
    product_id, _ = _product_fixture(token, "inactive")
    with SessionLocal() as reader:
        cached = reader.query(Product).filter(Product.id == product_id).one()
        assert cached.active is True
        with SessionLocal() as writer:
            current = writer.query(Product).filter(Product.id == product_id).one()
            current.active = False
            writer.commit()
        try:
            load_product_price_quotes(reader, [cached], lock=True)
        except HTTPException as exc:
            assert exc.status_code == 409
            return {"rejected_status": 409, "refreshed_active": bool(cached.active)}
        raise AssertionError("checkout pricing must reject a product deactivated before its authoritative lock")


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("checkout authoritative-state smoke requires PostgreSQL")
    token = uuid.uuid4().hex[:16]
    races = {
        2: _last_unit_race(token, 2, synchronize_preload=True),
        10: _last_unit_race(token, 10, synchronize_preload=False),
        20: _last_unit_race(token, 20, synchronize_preload=False),
    }
    price = _price_refresh(token)
    inactive = _deactivation_refresh(token)
    print(
        {
            "status": "ok",
            "last_unit_races": races,
            "price_refresh": price,
            "deactivation_refresh": inactive,
            "provider_calls": 0,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
