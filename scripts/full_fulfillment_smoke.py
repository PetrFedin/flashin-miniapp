#!/usr/bin/env python3
"""Prove the paid-order fulfillment and delivery lifecycle end to end.

The smoke uses the real FastAPI routes and PostgreSQL transaction boundaries.
All generated data is rolled back through an outer transaction after validating
picklist integrity, task ownership, shipment idempotency, audit and customer
notifications.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, joinedload

from backend.database import engine, get_db
from backend.main import app
from backend.models import (
    AdminUser,
    AuditLog,
    Customer,
    DeliveryShipment,
    FulfillmentTask,
    FulfillmentTaskItem,
    Notification,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    SlaEvent,
)
from backend.notification_models import NotificationEventKey
from backend.security import get_current_admin
from backend.services.fulfillment import ensure_fulfillment_task


def _expect(response, status_code: int, step: str) -> dict:
    if response.status_code != status_code:
        raise AssertionError(
            f"{step} returned HTTP {response.status_code}: {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise AssertionError(f"{step} returned a non-object payload: {payload!r}")
    return payload


def main() -> int:
    token = uuid.uuid4().hex[:16]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    previous_overrides = dict(app.dependency_overrides)
    client: TestClient | None = None

    try:
        admin = AdminUser(
            email=f"fulfillment-{token}@flashin.test",
            password_hash="unused",
            role="owner",
            active=True,
        )
        customer = Customer(
            telegram_id=str(int(token, 16)),
            username=f"fulfillment_{token}",
            first_name="Fulfillment",
        )
        product = Product(
            sku=f"FULL-{token}",
            title="Full fulfillment smoke product",
            slug=f"full-fulfillment-{token}",
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
            sku=f"FULL-V-{token}",
            stock_qty=5,
            reserved_qty=0,
        )
        db.add_all([admin, customer, product, variant])
        db.flush()
        order = Order(
            customer_id=customer.id,
            status="paid",
            payment_status="paid",
            delivery_status="not_started",
            total_amount=18000,
            discount_amount=0,
            currency="RUB",
            delivery_type="courier",
            address="Pilot address",
            comment="Full fulfillment transactional smoke",
        )
        db.add(order)
        db.flush()
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            title=product.title,
            size=variant.size,
            quantity=2,
            price=9000,
        )
        db.add(order_item)
        db.flush()
        order = (
            db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.customer))
            .filter(Order.id == order.id)
            .one()
        )
        task = ensure_fulfillment_task(db, order)
        db.commit()
        task_id = task.id
        order_id = order.id

        def override_db():
            yield db

        def override_admin():
            return admin

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_admin] = override_admin
        client = TestClient(app)

        tasks_response = client.get("/api/fulfillment/tasks")
        if tasks_response.status_code != 200:
            raise AssertionError(
                f"list fulfillment tasks returned HTTP {tasks_response.status_code}: "
                f"{tasks_response.text}"
            )
        assert any(item["id"] == task_id for item in tasks_response.json())

        picking = _expect(
            client.patch(
                f"/api/fulfillment/tasks/{task_id}",
                json={"status": "picking", "comment": "Pilot assembly started"},
            ),
            200,
            "start picking",
        )
        assert picking["status"] == "picking"
        assert picking["assigned_admin_id"] == admin.id

        rejected_pack = client.patch(
            f"/api/fulfillment/tasks/{task_id}",
            json={"status": "packed", "comment": "Too early"},
        )
        assert rejected_pack.status_code == 409
        assert (
            rejected_pack.json()["detail"]
            == "Every picklist item must be fully picked before packing"
        )

        picklist = _expect(
            client.get(f"/api/fulfillment/tasks/{task_id}/picklist"),
            200,
            "load picklist",
        )
        assert len(picklist["items"]) == 1
        task_item = picklist["items"][0]
        assert task_item["quantity"] == 2

        picked = _expect(
            client.patch(
                f"/api/fulfillment/task-items/{task_item['task_item_id']}"
                "?picked_qty=2&status=picked"
            ),
            200,
            "pick complete quantity",
        )
        assert picked["status"] == "picked"
        assert picked["picked_qty"] == picked["ordered_qty"] == 2

        packed = _expect(
            client.patch(
                f"/api/fulfillment/tasks/{task_id}",
                json={"status": "packed", "comment": "Every item picked"},
            ),
            200,
            "pack order",
        )
        assert packed["status"] == "packed"

        ready = _expect(
            client.patch(
                f"/api/fulfillment/tasks/{task_id}",
                json={"status": "ready", "comment": "Ready for courier"},
            ),
            200,
            "mark ready",
        )
        assert ready["status"] == "ready"

        shipment = _expect(
            client.post(
                f"/api/delivery-providers/orders/{order_id}/shipment"
                "?provider_code=courier"
            ),
            200,
            "create shipment",
        )
        shipment_id = shipment["id"]
        assert shipment["status"] == "created"

        repeated_shipment = _expect(
            client.post(
                f"/api/delivery-providers/orders/{order_id}/shipment"
                "?provider_code=courier"
            ),
            200,
            "idempotent shipment create",
        )
        assert repeated_shipment["id"] == shipment_id

        tracking_number = f"PILOT-{token.upper()}"
        shipped = _expect(
            client.patch(
                f"/api/delivery-providers/shipments/{shipment_id}"
                f"?tracking_number={tracking_number}&status=shipped"
            ),
            200,
            "ship order",
        )
        assert shipped["status"] == "shipped"
        assert shipped["tracking_number"] == tracking_number

        delivered = _expect(
            client.patch(
                f"/api/delivery-providers/shipments/{shipment_id}"
                f"?tracking_number={tracking_number}&status=delivered"
            ),
            200,
            "deliver order",
        )
        assert delivered["status"] == "delivered"

        db.expire_all()
        persisted_order = db.query(Order).filter(Order.id == order_id).one()
        persisted_task = db.query(FulfillmentTask).filter(FulfillmentTask.id == task_id).one()
        persisted_task_item = (
            db.query(FulfillmentTaskItem)
            .filter(FulfillmentTaskItem.task_id == task_id)
            .one()
        )
        persisted_shipments = (
            db.query(DeliveryShipment)
            .filter(DeliveryShipment.order_id == order_id)
            .all()
        )
        audit_actions = {
            item.action
            for item in db.query(AuditLog).filter(AuditLog.admin_id == admin.id).all()
        }
        notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .all()
        )
        event_keys = (
            db.query(NotificationEventKey)
            .filter(NotificationEventKey.event_key.like(f"order:{order_id}:%"))
            .all()
        )
        sla_events = db.query(SlaEvent).filter(SlaEvent.order_id == order_id).all()

        assert persisted_order.status == "completed"
        assert persisted_order.payment_status == "paid"
        assert persisted_order.delivery_status == "delivered"
        assert persisted_order.tracking_number == tracking_number
        assert persisted_task.status == "ready"
        assert persisted_task.assigned_admin_id == admin.id
        assert persisted_task.pick_started_at is not None
        assert persisted_task.packed_at is not None
        assert persisted_task.ready_at is not None
        assert persisted_task_item.status == "picked"
        assert persisted_task_item.picked_qty == 2
        assert len(persisted_shipments) == 1
        assert persisted_shipments[0].status == "delivered"
        assert persisted_shipments[0].tracking_number == tracking_number
        assert "fulfillment.task.update" in audit_actions
        assert "fulfillment.task_item.update" in audit_actions
        assert "delivery.shipment.create" in audit_actions
        assert "delivery.shipment.update" in audit_actions
        assert len(notifications) == 4
        assert len(event_keys) == 4
        assert all(event.status == "resolved" for event in sla_events)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "order_id": order_id,
                    "task_id": task_id,
                    "shipment_id": shipment_id,
                    "order_status": persisted_order.status,
                    "delivery_status": persisted_order.delivery_status,
                    "tracking_number": persisted_order.tracking_number,
                    "notifications": len(notifications),
                    "audit_actions": sorted(audit_actions),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if client is not None:
            client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())