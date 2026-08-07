#!/usr/bin/env python3
"""Exercise the internal provider spine through real PostgreSQL transactions.

The application/domain/database path is real. Only the external MoySklad HTTP
boundary is replaced with a deterministic local fake. This is a CI contract
smoke, not live Telegram/YooKassa/MoySklad evidence.
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
from backend.services import moysklad_outbound
from backend.services.delivery_providers import ensure_ready_shipment, transition_shipment
from backend.services.fulfillment import update_fulfillment_status
from backend.services.inventory import reserve_variant
from backend.services.payment_settlement import settle_paid_order
from backend.services.refund_state import apply_provider_refund_status


def main() -> int:
    token = uuid.uuid4().hex[:16]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    original_get_settings = moysklad_outbound.get_settings
    original_request_json = moysklad_outbound._request_json
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

        task = db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order_id).one()
        update_fulfillment_status(db, task, "picking", "Provider spine picking")
        task_item = (
            db.query(FulfillmentTaskItem)
            .filter(FulfillmentTaskItem.task_id == task.id)
            .one()
        )
        task_item.status = "picked"
        task_item.picked_qty = 2
        update_fulfillment_status(db, task, "packed", "Provider spine packed")
        update_fulfillment_status(db, task, "ready", "Provider spine ready")
        db.flush()

        shipment, created = ensure_ready_shipment(db, paid_order, "pickup")
        assert created is True
        transition_shipment(db, shipment, f"SPINE-{token}", "shipped")
        transition_shipment(db, shipment, shipment.tracking_number, "delivered")
        db.flush()

        ret = ReturnRequest(
            order_id=order_id,
            customer_id=customer.id,
            reason="Provider spine full refund",
            status="processing",
            provider_refund_id=f"refund-{token}",
            refund_amount=18000,
        )
        db.add(ret)
        db.flush()
        result = apply_provider_refund_status(db, ret, paid_order, "succeeded")
        assert result["inventory_restored"] is True
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
        commands = (
            db.query(ProviderCommand)
            .filter(ProviderCommand.aggregate_id.in_([str(order_id), str(ret.id)]))
            .order_by(ProviderCommand.id.asc())
            .all()
        )

        assert refunded_order.status == "refunded"
        assert refunded_order.payment_status == "refunded"
        assert refunded_order.delivery_status == "delivered"
        assert persisted_shipment.status == "delivered"
        assert persisted_return.status == "approved"
        assert persisted_variant.stock_qty == 5
        assert persisted_variant.reserved_qty == 0
        assert [command.command_type for command in commands] == [
            "moysklad.customer_order.create",
            "moysklad.demand.create",
            "moysklad.sales_return.create",
        ]
        assert all(command.status == "pending" for command in commands)

        worker_result = asyncio.run(process_provider_commands(db, limit=20))
        assert worker_result["claimed"] == 3
        assert worker_result["sent"] == 3
        assert worker_result["review_required"] == 0
        assert worker_result["failed"] == 0

        db.expire_all()
        sent_commands = (
            db.query(ProviderCommand)
            .filter(ProviderCommand.aggregate_id.in_([str(order_id), str(ret.id)]))
            .order_by(ProviderCommand.id.asc())
            .all()
        )
        assert [command.status for command in sent_commands] == ["sent", "sent", "sent"]
        assert [command.external_id for command in sent_commands] == [
            "ms-customer-order-ci",
            "ms-demand-ci",
            "ms-sales-return-ci",
        ]
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

        replay_result = apply_provider_refund_status(
            db,
            persisted_return,
            refunded_order,
            "succeeded",
        )
        assert replay_result["idempotent"] is True
        db.commit()
        assert asyncio.run(process_provider_commands(db, limit=20))["claimed"] == 0

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
                    "stock_after_full_refund": persisted_variant.stock_qty,
                    "provider_commands": [
                        {
                            "type": command.command_type,
                            "status": command.status,
                            "external_id": command.external_id,
                        }
                        for command in sent_commands
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
        moysklad_outbound.get_settings = original_get_settings
        moysklad_outbound._request_json = original_request_json
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
