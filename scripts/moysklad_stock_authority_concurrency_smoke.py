#!/usr/bin/env python3
"""Prove blocked MoySklad stock evidence is single-open under PostgreSQL races."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine  # noqa: E402
from backend.models import (  # noqa: E402
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
from backend.reverse_logistics_models import (  # noqa: E402
    ReturnLogisticsCase,
    ReturnLogisticsEvent,
    ReturnLogisticsItem,
)
from backend.services.moysklad_stock_authority import (  # noqa: E402
    evaluate_moysklad_stock_snapshot,
)

WORKERS = 12


def _seed() -> tuple[int, int, str]:
    suffix = uuid4().hex[:16]
    db = SessionLocal()
    try:
        customer = Customer(telegram_id=f"authority-race-{suffix}")
        product = Product(
            sku=f"AUTH-{suffix}",
            title="Authority concurrency proof",
            slug=f"authority-concurrency-{suffix}",
            price=1000,
        )
        db.add_all([customer, product])
        db.flush()

        provider_id = f"ms-authority-{suffix}"
        variant = ProductVariant(
            product_id=product.id,
            size="M",
            sku=f"AUTH-{suffix}-M",
            moysklad_id=provider_id,
            stock_qty=5,
            reserved_qty=0,
        )
        db.add(variant)
        db.flush()

        order = Order(
            customer_id=customer.id,
            status="refunded",
            payment_status="refunded",
            total_amount=1000,
            currency="RUB",
        )
        db.add(order)
        db.flush()
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            title="Authority concurrency proof",
            size="M",
            quantity=1,
            price=1000,
        )
        db.add(order_item)
        db.flush()

        return_request = ReturnRequest(
            order_id=order.id,
            customer_id=customer.id,
            reason="authority concurrency proof",
            status="approved",
            refund_amount=0,
        )
        db.add(return_request)
        db.flush()
        case = ReturnLogisticsCase(
            return_request_id=return_request.id,
            order_id=order.id,
            customer_id=customer.id,
            status="inspected",
        )
        db.add(case)
        db.flush()
        physical_item = ReturnLogisticsItem(
            case_id=case.id,
            order_item_id=order_item.id,
            variant_id=variant.id,
            ordered_qty=1,
            authorized_qty=1,
            received_qty=1,
            inspected_qty=1,
            resalable_qty=1,
            damaged_qty=0,
            quarantine_qty=0,
        )
        db.add(physical_item)
        db.flush()
        event = ReturnLogisticsEvent(
            case_id=case.id,
            item_id=physical_item.id,
            event_type="inspected",
            disposition="resalable",
            quantity=1,
            idempotency_key=f"authority-race-{suffix}",
            payload_hash="c" * 64,
            reason="authority concurrency proof",
        )
        db.add(event)
        db.flush()

        variant.stock_qty = 6
        db.add(
            InventoryMovement(
                order_id=order.id,
                variant_id=variant.id,
                kind="return",
                quantity=1,
                stock_before=5,
                stock_after=6,
                reserved_before=0,
                reserved_after=0,
                source=f"reverse_logistics_event:{event.id}",
            )
        )
        db.commit()
        return int(variant.id), int(event.id), provider_id
    finally:
        db.close()


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError(
            "MoySklad stock authority concurrency proof requires PostgreSQL"
        )

    variant_id, event_id, provider_id = _seed()
    barrier = Barrier(WORKERS)

    def observe(worker_index: int) -> tuple[bool, int, tuple[int, ...]]:
        db = SessionLocal()
        try:
            variant = db.get(ProductVariant, variant_id)
            if variant is None:
                raise AssertionError("Seeded ProductVariant disappeared")
            barrier.wait(timeout=10)
            external_stock = 5 if worker_index % 2 == 0 else 4
            decision = evaluate_moysklad_stock_snapshot(
                db,
                variant,
                external_stock,
            )
            # The evidence transaction must outlive caller rollback.
            db.rollback()
            return (
                bool(decision.blocked),
                int(decision.target_stock),
                tuple(decision.pending_event_ids),
            )
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        decisions = list(pool.map(observe, range(WORKERS)))

    expected = (True, 6, (event_id,))
    if any(decision != expected for decision in decisions):
        raise AssertionError(f"Unexpected authority decisions: {decisions!r}")

    verify = SessionLocal()
    try:
        conflicts = (
            verify.query(MoySkladConflict)
            .filter(
                MoySkladConflict.moysklad_id == provider_id,
                MoySkladConflict.conflict_type
                == "stale_stock_pending_physical_return",
                MoySkladConflict.status == "open",
            )
            .all()
        )
        blocked = (
            verify.query(StockReconciliationLog)
            .filter(
                StockReconciliationLog.variant_id == variant_id,
                StockReconciliationLog.action == "blocked_physical_return",
                StockReconciliationLog.status == "open",
            )
            .all()
        )
        if len(conflicts) != 1:
            raise AssertionError(
                f"Expected one open MoySklad conflict, found {len(conflicts)}"
            )
        if len(blocked) != 1:
            raise AssertionError(
                f"Expected one open blocked reconciliation, found {len(blocked)}"
            )
        if str(event_id) not in conflicts[0].message:
            raise AssertionError("Open conflict lost pending physical event evidence")
        if blocked[0].external_stock_qty not in {4, 5}:
            raise AssertionError("Blocked evidence contains an impossible provider stock")
    finally:
        verify.close()

    print(
        "moysklad stock authority concurrency: PASS "
        f"({WORKERS} concurrent blocked snapshots -> one open evidence pair)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
