#!/usr/bin/env python3
"""PostgreSQL proof for MoySklad damaged/quarantine disposition authority."""

from __future__ import annotations

import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine
from backend.models import (
    Customer,
    InventoryMovement,
    MoySkladConflict,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    ReturnRequest,
)
from backend.provider_models import ProviderCommand
from backend.reverse_logistics_models import ReturnLogisticsEvent, ReturnLogisticsItem
from backend.services import moysklad_reverse_return as reverse_moysklad
from backend.services.reverse_logistics import (
    authorize_item,
    ensure_physical_case,
    inspect_item,
    mark_in_transit,
    receive_item,
    resolve_quarantine_item,
)
from backend.services.stock_reconciliation import reconcile_stock_rows


def _settings():
    return SimpleNamespace(
        moysklad_mode="live",
        moysklad_order_export_enabled=True,
        moysklad_store_id="sellable-store",
        moysklad_damaged_store_id="damaged-store",
        moysklad_quarantine_store_id="quarantine-store",
    )


def _fixture(token: str) -> tuple[int, int, int]:
    with SessionLocal() as db:
        customer = Customer(telegram_id=f"disp-smoke-{token}")
        product = Product(
            sku=f"DISP-P-{token}",
            slug=f"disp-p-{token}",
            title="Disposition smoke",
            price=100,
            currency="RUB",
            active=True,
            moysklad_id=f"product-{token}",
        )
        variant = ProductVariant(
            product=product,
            size="M",
            sku=f"DISP-V-{token}",
            stock_qty=10,
            reserved_qty=0,
            moysklad_id=f"variant-{token}",
        )
        db.add_all([customer, product, variant])
        db.flush()
        order = Order(
            customer_id=customer.id,
            status="completed",
            payment_status="paid",
            delivery_status="delivered",
            total_amount=300,
            delivery_price=0,
            discount_amount=0,
            loyalty_discount_amount=0,
            currency="RUB",
            delivery_type="pickup",
        )
        db.add(order)
        db.flush()
        item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            title=product.title,
            size="M",
            quantity=3,
            price=100,
        )
        ret = ReturnRequest(
            order_id=order.id,
            customer_id=customer.id,
            reason="mixed disposition postgres proof",
            status="approved",
            refund_amount=300,
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
                external_id=f"demand-{token}",
            )
        )
        db.commit()

        case = ensure_physical_case(db, ret=ret, order=order)
        physical = (
            db.query(ReturnLogisticsItem)
            .filter(ReturnLogisticsItem.case_id == case.id)
            .one()
        )
        authorize_item(
            db,
            case_id=case.id,
            item_id=physical.id,
            quantity=3,
            idempotency_key=f"authorize-{token}",
            actor_admin_id=None,
        )
        mark_in_transit(db, case_id=case.id)
        receive_item(
            db,
            case_id=case.id,
            item_id=physical.id,
            quantity=3,
            idempotency_key=f"receive-{token}",
            actor_admin_id=None,
        )
        for index, disposition in enumerate(("resalable", "damaged", "quarantine"), start=1):
            inspect_item(
                db,
                case_id=case.id,
                item_id=physical.id,
                quantity=1,
                disposition=disposition,
                idempotency_key=f"inspect-{index}-{token}",
                actor_admin_id=None,
            )
        db.commit()
        return int(case.id), int(physical.id), int(variant.id)


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("MoySklad disposition authority smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:12]
    case_id, physical_id, variant_id = _fixture(token)
    original_settings = reverse_moysklad.get_settings
    reverse_moysklad.get_settings = _settings
    try:
        with SessionLocal() as db:
            reverse_moysklad.enqueue_moysklad_physical_sales_return(db, case_id)
            db.commit()
            commands = (
                db.query(ProviderCommand)
                .filter(
                    ProviderCommand.provider == "moysklad",
                    ProviderCommand.aggregate_type == "return_logistics_case",
                    ProviderCommand.aggregate_id == str(case_id),
                    ProviderCommand.command_type.like("moysklad.physical_sales_return%"),
                )
                .order_by(ProviderCommand.id.asc())
                .all()
            )
            assert len(commands) == 3
            evidence = {
                json.loads(command.payload_json)["provider_disposition"]: (
                    json.loads(command.payload_json)["provider_store_id"],
                    command,
                )
                for command in commands
            }
            assert {key: value[0] for key, value in evidence.items()} == {
                "resalable": "sellable-store",
                "damaged": "damaged-store",
                "quarantine": "quarantine-store",
            }
            assert ":resalable:sales_return:v2" in evidence["resalable"][1].idempotency_key

            for disposition, (_store, command) in evidence.items():
                command.status = "sent"
                command.external_id = f"{disposition}-{token}"
            db.commit()

            variant = db.get(ProductVariant, variant_id)
            assert int(variant.stock_qty) == 11
            physical = db.get(ReturnLogisticsItem, physical_id)
            assert (
                int(physical.resalable_qty),
                int(physical.damaged_qty),
                int(physical.quarantine_qty),
            ) == (1, 1, 1)

        def resolve_worker() -> tuple[bool, int, int]:
            with SessionLocal() as db:
                result = resolve_quarantine_item(
                    db,
                    case_id=case_id,
                    item_id=physical_id,
                    quantity=1,
                    disposition="resalable",
                    idempotency_key=f"resolve-{token}",
                    actor_admin_id=None,
                    reason="QC passed",
                )
                command = reverse_moysklad.enqueue_moysklad_quarantine_move(
                    db,
                    result.event.id,
                )
                db.commit()
                return bool(result.idempotent), int(result.event.id), int(command.id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _n: resolve_worker(), range(2)))

        assert sorted(flag for flag, _event, _command in outcomes) == [False, True]
        assert len({event for _flag, event, _command in outcomes}) == 1
        assert len({command for _flag, _event, command in outcomes}) == 1

        with SessionLocal() as db:
            physical = db.get(ReturnLogisticsItem, physical_id)
            variant = db.get(ProductVariant, variant_id)
            assert (
                int(physical.resalable_qty),
                int(physical.damaged_qty),
                int(physical.quarantine_qty),
            ) == (2, 1, 0)
            assert int(variant.stock_qty) == 12

            reclassified = (
                db.query(ReturnLogisticsEvent)
                .filter(
                    ReturnLogisticsEvent.case_id == case_id,
                    ReturnLogisticsEvent.event_type == "reclassified",
                )
                .one()
            )
            assert (
                db.query(InventoryMovement)
                .filter(
                    InventoryMovement.variant_id == variant_id,
                    InventoryMovement.source == f"reverse_logistics_event:{reclassified.id}",
                )
                .count()
                == 1
            )
            move = (
                db.query(ProviderCommand)
                .filter(
                    ProviderCommand.provider == "moysklad",
                    ProviderCommand.command_type == "moysklad.quarantine_move.create",
                    ProviderCommand.aggregate_id == str(reclassified.id),
                )
                .one()
            )
            move_payload = json.loads(move.payload_json)
            assert move_payload["source_store_id"] == "quarantine-store"
            assert move_payload["target_store_id"] == "sellable-store"

            # Initial resalable provider receipt is sent, but the quarantine move
            # is not. A sellable-store snapshot at 11 therefore cannot erase the
            # locally verified second sellable unit.
            reconcile_stock_rows(
                db,
                [{"sku": variant.sku, "stock_qty": 11}],
                apply=True,
            )
            db.refresh(variant)
            assert int(variant.stock_qty) == 12
            assert (
                db.query(MoySkladConflict)
                .filter(
                    MoySkladConflict.moysklad_id == variant.moysklad_id,
                    MoySkladConflict.status == "open",
                )
                .count()
                == 1
            )

            move.status = "sent"
            move.external_id = f"move-{token}"
            db.commit()

            reconcile_stock_rows(
                db,
                [{"sku": variant.sku, "stock_qty": 12}],
                apply=True,
            )
            db.refresh(variant)
            assert int(variant.stock_qty) == 12
            assert (
                db.query(MoySkladConflict)
                .filter(
                    MoySkladConflict.moysklad_id == variant.moysklad_id,
                    MoySkladConflict.status == "open",
                )
                .count()
                == 0
            )

        print({
            "status": "ok",
            "mixed_disposition": {"resalable": 1, "damaged": 1, "quarantine": 1},
            "provider_stores_separated": True,
            "local_sellable_increment_before_quarantine_resolution": 1,
            "quarantine_resolution_concurrency": "one event / one movement / one command",
            "local_sellable_increment_after_qc_release": 2,
            "stale_sellable_snapshot_blocked_before_move_confirmation": True,
            "provider_catchup_after_move": True,
        })
        return 0
    finally:
        reverse_moysklad.get_settings = original_settings


if __name__ == "__main__":
    raise SystemExit(main())
