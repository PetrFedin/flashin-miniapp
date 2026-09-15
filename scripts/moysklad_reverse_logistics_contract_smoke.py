#!/usr/bin/env python3
"""Prove MoySklad reverse-logistics payload and stock-sync authority boundaries."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine
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
from backend.provider_models import ProviderCommand
from backend.reverse_logistics_models import ReturnLogisticsItem
from backend.services import moysklad_reverse_return as reverse_moysklad
from backend.services.moysklad_outbound import MoySkladReviewRequired
from backend.services.reverse_logistics import (
    authorize_item,
    ensure_physical_case,
    inspect_item,
    mark_in_transit,
    receive_item,
)
from backend.services.stock_reconciliation import reconcile_stock_rows


def _fixture(
    db,
    token: str,
    suffix: str,
    *,
    ordered_qty: int,
    returned_qty: int,
    dispositions: list[tuple[int, str]],
):
    customer = Customer(telegram_id=f"moy-reverse-{token}-{suffix}", first_name="Moy")
    product = Product(
        sku=f"MOY-REV-P-{token}-{suffix}",
        slug=f"moy-rev-{token}-{suffix}",
        title="Moy reverse item",
        price=100,
        currency="RUB",
        active=True,
        moysklad_id=f"product-{token}-{suffix}",
    )
    variant = ProductVariant(
        product=product,
        size="M",
        color="Black",
        sku=f"MOY-REV-V-{token}-{suffix}",
        stock_qty=10,
        reserved_qty=0,
        moysklad_id=f"variant-{token}-{suffix}",
    )
    db.add_all([customer, product, variant])
    db.flush()

    order = Order(
        customer_id=customer.id,
        status="completed",
        payment_status="paid",
        delivery_status="delivered",
        total_amount=ordered_qty * 100,
        delivery_price=0,
        discount_amount=0,
        loyalty_points_redeemed=0,
        loyalty_discount_amount=0,
        currency="RUB",
        delivery_type="pickup",
        address="Самовывоз",
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        variant_id=variant.id,
        title=product.title,
        size="M",
        quantity=ordered_qty,
        price=100,
    )
    ret = ReturnRequest(
        order_id=order.id,
        customer_id=customer.id,
        reason="MoySklad reverse contract",
        status="approved",
        provider_refund_id=f"refund-{token}-{suffix}",
        refund_amount=returned_qty * 100,
    )
    db.add_all([item, ret])
    db.flush()
    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="moysklad.demand.create",
            idempotency_key=f"order:{order.id}:demand:v1",
            aggregate_type="order",
            aggregate_id=str(order.id),
            payload_json="{}",
            status="sent",
            attempts=1,
            external_id=f"demand-{token}-{suffix}",
        )
    )
    db.commit()

    order = db.query(Order).filter(Order.id == order.id).one()
    ret = db.query(ReturnRequest).filter(ReturnRequest.id == ret.id).one()
    case = ensure_physical_case(db, ret=ret, order=order)
    physical = db.query(ReturnLogisticsItem).filter(ReturnLogisticsItem.case_id == case.id).one()
    authorize_item(
        db,
        case_id=case.id,
        item_id=physical.id,
        quantity=returned_qty,
        idempotency_key=f"authorize-{token}-{suffix}",
        actor_admin_id=None,
    )
    mark_in_transit(db, case_id=case.id)
    receive_item(
        db,
        case_id=case.id,
        item_id=physical.id,
        quantity=returned_qty,
        idempotency_key=f"receive-{token}-{suffix}",
        actor_admin_id=None,
    )
    for index, (quantity, disposition) in enumerate(dispositions, start=1):
        inspect_item(
            db,
            case_id=case.id,
            item_id=physical.id,
            quantity=quantity,
            disposition=disposition,
            idempotency_key=f"inspect-{index}-{token}-{suffix}",
            actor_admin_id=None,
        )
    db.commit()
    return int(case.id)


def _variant_for_case(db, case_id: int) -> ProductVariant:
    physical = (
        db.query(ReturnLogisticsItem)
        .filter(ReturnLogisticsItem.case_id == int(case_id))
        .one()
    )
    return db.query(ProductVariant).filter(ProductVariant.id == physical.variant_id).one()


def _add_physical_provider_command(db, case_id: int, token: str, suffix: str) -> None:
    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="moysklad.physical_sales_return.create",
            idempotency_key=f"physical-return:{int(case_id)}:sales_return:v1",
            aggregate_type="return_logistics_case",
            aggregate_id=str(int(case_id)),
            payload_json=f'{{"case_id":{int(case_id)}}}',
            status="sent",
            attempts=1,
            external_id=f"sales-return-{token}-{suffix}",
        )
    )
    db.commit()


def _all_resalable_partial(token: str) -> dict[str, int]:
    with SessionLocal() as db:
        case_id = _fixture(
            db,
            token,
            "partial-resalable",
            ordered_qty=3,
            returned_qty=2,
            dispositions=[(2, "resalable")],
        )

    with SessionLocal() as db:
        snapshot = reverse_moysklad._prepare_physical_return_snapshot(db, case_id)

    assert len(snapshot.lines) == 1
    assert snapshot.lines[0].quantity == 2
    assert snapshot.lines[0].net_total_cents == 20000

    async def fake_assortment(moysklad_id: str) -> dict:
        return {"href": f"https://example.invalid/entity/variant/{moysklad_id}"}

    original = reverse_moysklad._resolve_assortment_meta
    reverse_moysklad._resolve_assortment_meta = fake_assortment
    try:
        positions = asyncio.run(reverse_moysklad._physical_return_positions(snapshot))
    finally:
        reverse_moysklad._resolve_assortment_meta = original

    assert sum(int(position["quantity"]) for position in positions) == 2
    assert sum(
        int(position["quantity"]) * int(position["price"])
        for position in positions
    ) == 20000
    return {"quantity": 2, "net_total_cents": 20000}


def _stale_stock_sync_guard(token: str) -> dict[str, int]:
    with SessionLocal() as db:
        case_id = _fixture(
            db,
            token,
            "stale-sync",
            ordered_qty=2,
            returned_qty=1,
            dispositions=[(1, "resalable")],
        )
        variant = _variant_for_case(db, case_id)
        variant_id = int(variant.id)
        sku = str(variant.sku)
        moysklad_id = str(variant.moysklad_id)
        assert int(variant.stock_qty) == 11

        # A stale provider snapshot cannot erase warehouse-confirmed resalable stock.
        reconcile_stock_rows(db, [{"sku": sku, "stock_qty": 10}], apply=True)
        db.refresh(variant)
        assert int(variant.stock_qty) == 11
        assert (
            db.query(MoySkladConflict)
            .filter(
                MoySkladConflict.moysklad_id == moysklad_id,
                MoySkladConflict.conflict_type == "stale_stock_pending_physical_return",
                MoySkladConflict.status == "open",
            )
            .count()
            == 1
        )
        assert (
            db.query(StockReconciliationLog)
            .filter(
                StockReconciliationLog.variant_id == variant_id,
                StockReconciliationLog.action == "blocked_physical_return",
                StockReconciliationLog.status == "open",
            )
            .count()
            == 1
        )

        # A sent provider command alone is not stock catch-up evidence.
        _add_physical_provider_command(db, case_id, token, "stale-sync")
        reconcile_stock_rows(db, [{"sku": sku, "stock_qty": 10}], apply=True)
        db.refresh(variant)
        assert int(variant.stock_qty) == 11
        assert (
            db.query(StockReconciliationLog)
            .filter(
                StockReconciliationLog.variant_id == variant_id,
                StockReconciliationLog.action == "physical_return_catchup",
            )
            .count()
            == 0
        )

        # Only an observed inbound snapshot at the physical ledger floor clears the guard.
        reconcile_stock_rows(db, [{"sku": sku, "stock_qty": 11}], apply=True)
        db.refresh(variant)
        assert int(variant.stock_qty) == 11
        assert (
            db.query(MoySkladConflict)
            .filter(
                MoySkladConflict.moysklad_id == moysklad_id,
                MoySkladConflict.status == "open",
            )
            .count()
            == 0
        )
        assert (
            db.query(StockReconciliationLog)
            .filter(
                StockReconciliationLog.variant_id == variant_id,
                StockReconciliationLog.action == "physical_return_catchup",
                StockReconciliationLog.status == "resolved",
            )
            .count()
            == 1
        )

        # The protection is not a permanent floor after provider catch-up.
        reconcile_stock_rows(db, [{"sku": sku, "stock_qty": 9}], apply=True)
        db.refresh(variant)
        assert int(variant.stock_qty) == 9

    return {
        "protected_local_stock": 11,
        "stale_provider_stock": 10,
        "provider_catchup_stock": 11,
        "normal_sync_after_catchup": 9,
    }


def _non_sellable_does_not_create_sync_guard(token: str) -> dict[str, int]:
    with SessionLocal() as db:
        case_id = _fixture(
            db,
            token,
            "non-sellable-sync",
            ordered_qty=2,
            returned_qty=2,
            dispositions=[(1, "damaged"), (1, "quarantine")],
        )
        variant = _variant_for_case(db, case_id)
        moysklad_id = str(variant.moysklad_id)
        assert int(variant.stock_qty) == 10

        reconcile_stock_rows(db, [{"sku": variant.sku, "stock_qty": 8}], apply=True)
        db.refresh(variant)
        assert int(variant.stock_qty) == 8
        assert (
            db.query(MoySkladConflict)
            .filter(
                MoySkladConflict.moysklad_id == moysklad_id,
                MoySkladConflict.conflict_type == "stale_stock_pending_physical_return",
                MoySkladConflict.status == "open",
            )
            .count()
            == 0
        )

    return {"local_before_sync": 10, "provider_stock_applied": 8}


def _non_resalable_fails_closed_and_keeps_resalable_guard(token: str) -> str:
    with SessionLocal() as db:
        case_id = _fixture(
            db,
            token,
            "mixed-disposition",
            ordered_qty=3,
            returned_qty=3,
            dispositions=[(1, "resalable"), (1, "damaged"), (1, "quarantine")],
        )

    with SessionLocal() as db:
        try:
            reverse_moysklad._prepare_physical_return_snapshot(db, case_id)
        except MoySkladReviewRequired as exc:
            message = str(exc)
            assert "damaged/quarantine" in message.lower(), message

            # Provider export is unresolved/review-required, so the verified
            # resalable unit must remain protected from a stale absolute snapshot.
            variant = _variant_for_case(db, case_id)
            assert int(variant.stock_qty) == 11
            reconcile_stock_rows(
                db,
                [{"sku": variant.sku, "stock_qty": 10}],
                apply=True,
            )
            db.refresh(variant)
            assert int(variant.stock_qty) == 11
            assert (
                db.query(MoySkladConflict)
                .filter(
                    MoySkladConflict.moysklad_id == str(variant.moysklad_id),
                    MoySkladConflict.conflict_type == "stale_stock_pending_physical_return",
                    MoySkladConflict.status == "open",
                )
                .count()
                == 1
            )
            return message
    raise AssertionError("mixed disposition must require provider reconciliation")


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("MoySklad reverse logistics contract smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:12]
    partial = _all_resalable_partial(token)
    stale_guard = _stale_stock_sync_guard(token)
    non_sellable_sync = _non_sellable_does_not_create_sync_guard(token)
    review = _non_resalable_fails_closed_and_keeps_resalable_guard(token)
    print(
        {
            "status": "ok",
            "partial_resalable_outbound": partial,
            "stale_provider_stock_guard": stale_guard,
            "non_sellable_stock_sync": non_sellable_sync,
            "damaged_quarantine_provider_outcome": "review_required_before_external_io",
            "mixed_resalable_stock_guard": "protected_until_provider_reconciliation",
            "review_reason": review,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
