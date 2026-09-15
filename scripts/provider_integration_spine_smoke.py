#!/usr/bin/env python3
"""Exercise the internal provider spine through real PostgreSQL transactions.

The application/domain/database path is real. Only the external MoySklad HTTP
boundary is replaced with a deterministic local fake. This is a CI contract
smoke, not live Telegram/YooKassa/MoySklad evidence.

The smoke deliberately proves two distinct authorities:
1. a completed financial refund changes money/loyalty state but does not restore
   inventory or create a MoySklad SalesReturn;
2. a received + inspected + resalable physical return restores inventory once
   and is the only authority that creates the MoySklad SalesReturn.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.orm import Session, joinedload

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import engine
from backend.jobs.provider_command_jobs import process_provider_commands
from backend.models import (
    Customer,
    DeliveryShipment,
    FulfillmentTask,
    FulfillmentTaskItem,
    InventoryMovement,
    Notification,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    ReturnRequest,
)
from backend.provider_models import ProviderCommand
from backend.reverse_logistics_models import ReturnLogisticsItem
from backend.services import moysklad_outbound, moysklad_reverse_return
from backend.services.delivery_authority import accept_quote_for_order, create_delivery_quote
from backend.services.delivery_providers import ensure_ready_shipment, transition_shipment
from backend.services.fulfillment import update_fulfillment_status
from backend.services.fulfillment_locking import lock_fulfillment_task_for_update
from backend.services.inventory import reserve_variant
from backend.services.moysklad_reverse_return import enqueue_moysklad_physical_sales_return
from backend.services.payment_settlement import settle_paid_order
from backend.services.refund_state import apply_provider_refund_status
from backend.services.reverse_logistics import (
    authorize_item,
    ensure_physical_case,
    inspect_item,
    mark_in_transit,
    receive_item,
)


def main() -> int:
    token = uuid.uuid4().hex[:16]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    original_outbound_get_settings = moysklad_outbound.get_settings
    original_outbound_request_json = moysklad_outbound._request_json
    original_reverse_get_settings = moysklad_reverse_return.get_settings
    original_reverse_request_json = moysklad_reverse_return._request_json
    provider_posts: list[tuple[str, dict]] = []

    fake_settings = SimpleNamespace(
        moysklad_order_export_enabled=True,
        moysklad_token="ci-provider-token",
        moysklad_login="",
        moysklad_password="",
        moysklad_base_url="https://moysklad.test/api/remap/1.2",
        moysklad_organization_id="organization-ci",
        moysklad_agent_id="counterparty-ci",
        moysklad_store_id="store-ci",
        moysklad_delivery_service_id="delivery-service-ci",
    )

    async def fake_request_json(method, path, *, json_body=None, params=None):
        if method == "GET" and path == "entity/assortment":
            filter_value = str((params or {}).get("filter") or "")
            item_id = filter_value.removeprefix("id=")
            if not item_id:
                raise AssertionError("MoySklad assortment lookup has no id filter")
            return {
                "rows": [
                    {
                        "id": item_id,
                        "meta": {
                            "href": f"https://moysklad.test/api/remap/1.2/entity/variant/{item_id}",
                            "type": "variant",
                            "mediaType": "application/json",
                        },
                    }
                ]
            }
        if method != "POST" or not isinstance(json_body, dict):
            raise AssertionError(f"Unexpected MoySklad request: {method} {path}")
        provider_posts.append((path, json_body))
        external_ids = {
            "entity/customerorder": "ms-customer-order-ci",
            "entity/demand": "ms-demand-ci",
            "entity/salesreturn": "ms-sales-return-ci",
        }
        try:
            external_id = external_ids[path]
        except KeyError as exc:
            raise AssertionError(f"Unexpected MoySklad POST path: {path}") from exc
        return {"id": external_id, "syncId": json_body.get("syncId")}

    try:
        moysklad_outbound.get_settings = lambda: fake_settings
        moysklad_outbound._request_json = fake_request_json
        moysklad_reverse_return.get_settings = lambda: fake_settings
        moysklad_reverse_return._request_json = fake_request_json

        customer = Customer(
            telegram_id=str(int(token, 16)),
            username=f"provider_spine_{token}",
            first_name="Provider Spine",
        )
        product = Product(
            sku=f"SPINE-{token}",
            moysklad_id=f"ms-product-{token}",
            title="Provider spine smoke product",
            slug=f"provider-spine-{token}",
            brand="FLASHIN",
            price=9000,
            currency="RUB",
            category="Testing",
            gender="unisex",
            active=True,
        )
        variant = ProductVariant(
            product=product,
            size="M",
            color="Black",
            sku=f"SPINE-V-{token}",
            moysklad_id=f"ms-variant-{token}",
            stock_qty=5,
            reserved_qty=0,
        )
        db.add_all([customer, product, variant])
        db.flush()

        order = Order(
            customer_id=customer.id,
            status="created",
            payment_status="pending",
            delivery_status="not_started",
            total_amount=18000,
            delivery_price=0,
            discount_amount=0,
            loyalty_points_redeemed=0,
            loyalty_discount_amount=0,
            currency="RUB",
            delivery_type="pickup",
            address="",
            comment="Provider spine CI smoke",
        )
        db.add(order)
        db.flush()
        quote = create_delivery_quote(
            db,
            customer_id=int(customer.id),
            delivery_type="pickup",
        )
        accept_quote_for_order(quote, order)
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                variant_id=variant.id,
                title=product.title,
                size=variant.size,
                quantity=2,
                price=9000,
            )
        )
        db.flush()
        reserve_variant(
            db,
            variant.id,
            2,
            order_id=order.id,
            source="provider_spine_smoke",
        )
        db.flush()

        order_id = order.id
        order = (
            db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.customer))
            .filter(Order.id == order_id)
            .one()
        )
        assert settle_paid_order(db, order) is True
        db.commit()

        db.expire_all()
        paid_order = (
            db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.customer))
            .filter(Order.id == order_id)
            .one()
        )
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        assert paid_order.status == "paid"
        assert paid_order.payment_status == "paid"
        assert persisted_variant.stock_qty == 3
        assert persisted_variant.reserved_qty == 0

        task_id = (
            db.query(FulfillmentTask.id)
            .filter(FulfillmentTask.order_id == order_id)
            .scalar()
        )
        assert task_id is not None
        fulfillment_order, task = lock_fulfillment_task_for_update(db, int(task_id))
        update_fulfillment_status(
            db,
            fulfillment_order,
            task,
            "picking",
            "Provider spine picking",
        )
        task_item = (
            db.query(FulfillmentTaskItem)
            .filter(FulfillmentTaskItem.task_id == task.id)
            .one()
        )
        task_item.status = "picked"
        task_item.picked_qty = 2
        update_fulfillment_status(
            db,
            fulfillment_order,
            task,
            "packed",
            "Provider spine packed",
        )
        update_fulfillment_status(
            db,
            fulfillment_order,
            task,
            "ready",
            "Provider spine ready",
        )
        db.flush()

        shipment, created = ensure_ready_shipment(db, paid_order, "pickup")
        assert created is True
        transition_shipment(db, paid_order, shipment, f"SPINE-{token}", "shipped")
        transition_shipment(db, paid_order, shipment, shipment.tracking_number, "delivered")
        db.flush()

        ret = ReturnRequest(
            order_id=order_id,
            customer_id=customer.id,
            reason="Provider spine full financial refund",
            status="processing",
            provider_refund_id=f"refund-{token}",
            refund_amount=18000,
        )
        db.add(ret)
        db.flush()
        result = apply_provider_refund_status(db, ret, paid_order, "succeeded")
        assert result["inventory_effect"] == "none_financial_refund_is_not_physical_return"
        assert result["physical_return_required_for_stock"] is True
        db.commit()

        db.expire_all()
        refunded_order = (
            db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.customer))
            .filter(Order.id == order_id)
            .one()
        )
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        persisted_return = db.query(ReturnRequest).filter(ReturnRequest.id == ret.id).one()
        persisted_shipment = db.query(DeliveryShipment).filter(DeliveryShipment.order_id == order_id).one()
        financial_commands = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.aggregate_type == "order",
                ProviderCommand.aggregate_id == str(order_id),
            )
            .order_by(ProviderCommand.id.asc())
            .all()
        )

        assert refunded_order.status == "refunded"
        assert refunded_order.payment_status == "refunded"
        assert refunded_order.delivery_status == "delivered"
        assert persisted_shipment.status == "delivered"
        assert persisted_return.status == "approved"
        assert persisted_variant.stock_qty == 3
        assert persisted_variant.reserved_qty == 0
        assert [command.command_type for command in financial_commands] == [
            "moysklad.customer_order.create",
            "moysklad.demand.create",
        ]
        assert all(command.status == "pending" for command in financial_commands)
        assert (
            db.query(ProviderCommand)
            .filter(ProviderCommand.command_type == "moysklad.sales_return.create")
            .count()
            == 0
        )

        # First complete the paid-order/demand provider spine. The physical
        # SalesReturn is not allowed to run until the demand dependency exists.
        worker_result = asyncio.run(process_provider_commands(db, limit=20))
        assert worker_result["claimed"] == 2
        assert worker_result["sent"] == 2
        assert worker_result["review_required"] == 0
        assert worker_result["failed"] == 0
        assert [path for path, _payload in provider_posts] == [
            "entity/customerorder",
            "entity/demand",
        ]

        db.expire_all()
        refunded_order = (
            db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.customer))
            .filter(Order.id == order_id)
            .one()
        )
        persisted_return = db.query(ReturnRequest).filter(ReturnRequest.id == ret.id).one()
        case = ensure_physical_case(db, ret=persisted_return, order=refunded_order)
        physical_item = (
            db.query(ReturnLogisticsItem)
            .filter(ReturnLogisticsItem.case_id == case.id)
            .one()
        )
        physical_key_prefix = f"spine-{token}"
        authorize_item(
            db,
            case_id=case.id,
            item_id=physical_item.id,
            quantity=2,
            idempotency_key=f"{physical_key_prefix}-authorize",
            actor_admin_id=None,
            reason="Warehouse-authorized full physical return",
        )
        mark_in_transit(db, case_id=case.id)
        receive_item(
            db,
            case_id=case.id,
            item_id=physical_item.id,
            quantity=2,
            idempotency_key=f"{physical_key_prefix}-receive",
            actor_admin_id=None,
            reason="Warehouse received two units",
        )
        inspection = inspect_item(
            db,
            case_id=case.id,
            item_id=physical_item.id,
            quantity=2,
            disposition="resalable",
            idempotency_key=f"{physical_key_prefix}-inspect",
            actor_admin_id=None,
            reason="Both units inspected and resalable",
        )
        assert inspection.case.status == "inspected"
        physical_command = enqueue_moysklad_physical_sales_return(db, case.id)
        assert physical_command is not None
        db.commit()

        db.expire_all()
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        assert persisted_variant.stock_qty == 5
        assert persisted_variant.reserved_qty == 0
        queued_physical_command = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.aggregate_type == "return_logistics_case",
                ProviderCommand.aggregate_id == str(case.id),
                ProviderCommand.command_type == "moysklad.physical_sales_return.create",
            )
            .one()
        )
        assert queued_physical_command.status == "pending"

        physical_worker_result = asyncio.run(process_provider_commands(db, limit=20))
        assert physical_worker_result["claimed"] == 1
        assert physical_worker_result["sent"] == 1
        assert physical_worker_result["review_required"] == 0
        assert physical_worker_result["failed"] == 0

        db.expire_all()
        sent_order_commands = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.aggregate_type == "order",
                ProviderCommand.aggregate_id == str(order_id),
            )
            .order_by(ProviderCommand.id.asc())
            .all()
        )
        sent_physical_command = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.aggregate_type == "return_logistics_case",
                ProviderCommand.aggregate_id == str(case.id),
                ProviderCommand.command_type == "moysklad.physical_sales_return.create",
            )
            .one()
        )
        assert [command.status for command in sent_order_commands] == ["sent", "sent"]
        assert [command.external_id for command in sent_order_commands] == [
            "ms-customer-order-ci",
            "ms-demand-ci",
        ]
        assert sent_physical_command.status == "sent"
        assert sent_physical_command.external_id == "ms-sales-return-ci"
        assert [path for path, _payload in provider_posts] == [
            "entity/customerorder",
            "entity/demand",
            "entity/salesreturn",
        ]
        sync_ids = [str(payload.get("syncId") or "") for _path, payload in provider_posts]
        assert all(sync_ids)
        assert len(set(sync_ids)) == 3
        assert all(
            sum(int(position["price"]) * int(position["quantity"]) for position in payload["positions"])
            == 1_800_000
            for _path, payload in provider_posts
        )
        sales_return_payload = provider_posts[-1][1]
        assert sales_return_payload["demand"]["meta"]["href"].endswith(
            "/entity/demand/ms-demand-ci"
        )
        assert sum(int(position["quantity"]) for position in sales_return_payload["positions"]) == 2

        replay_result = apply_provider_refund_status(
            db,
            persisted_return,
            refunded_order,
            "succeeded",
        )
        assert replay_result["idempotent"] is True
        assert replay_result["inventory_effect"] == "none_financial_refund_is_not_physical_return"
        replay_inspection = inspect_item(
            db,
            case_id=case.id,
            item_id=physical_item.id,
            quantity=2,
            disposition="resalable",
            idempotency_key=f"{physical_key_prefix}-inspect",
            actor_admin_id=None,
            reason="Both units inspected and resalable",
        )
        assert replay_inspection.idempotent is True
        enqueue_moysklad_physical_sales_return(db, case.id)
        db.commit()
        assert asyncio.run(process_provider_commands(db, limit=20))["claimed"] == 0

        db.expire_all()
        final_variant = db.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
        assert final_variant.stock_qty == 5
        movement_kinds = [
            row.kind
            for row in db.query(InventoryMovement)
            .filter(InventoryMovement.order_id == order_id)
            .order_by(InventoryMovement.id.asc())
            .all()
        ]
        assert movement_kinds == ["reserve", "commit", "return"]
        assert (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .count()
            >= 5
        )

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "stock_after_financial_refund": 3,
                    "stock_after_physical_resalable_return": final_variant.stock_qty,
                    "financial_refund_sales_return_commands": 0,
                    "provider_commands": [
                        {
                            "type": command.command_type,
                            "status": command.status,
                            "external_id": command.external_id,
                        }
                        for command in [*sent_order_commands, sent_physical_command]
                    ],
                    "provider_posts": [path for path, _payload in provider_posts],
                    "inventory_movements": movement_kinds,
                    "external_boundary": "moysklad_http_fake_only",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        moysklad_outbound.get_settings = original_outbound_get_settings
        moysklad_outbound._request_json = original_outbound_request_json
        moysklad_reverse_return.get_settings = original_reverse_get_settings
        moysklad_reverse_return._request_json = original_reverse_request_json
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
