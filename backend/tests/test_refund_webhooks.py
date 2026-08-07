from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.api import refund_webhooks as refund_webhooks_api
from backend.database import engine, get_db
from backend.main import app
from backend.models import (
    Customer,
    InventoryMovement,
    Notification,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    ReturnRequest,
)


def test_refund_webhook_uses_authoritative_provider_state_and_is_idempotent():
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    previous_overrides = dict(app.dependency_overrides)
    original_fetch = refund_webhooks_api.fetch_yookassa_refund
    client = None

    try:
        customer = Customer(
            telegram_id="990001122",
            username="refund_webhook_test",
            first_name="Refund Webhook",
        )
        product = Product(
            sku="REFUND-WEBHOOK-P",
            title="Refund webhook product",
            slug="refund-webhook-product",
            brand="FLASHIN",
            price=1000,
            currency="RUB",
            category="Testing",
            gender="unisex",
            active=True,
        )
        variant = ProductVariant(
            product=product,
            size="M",
            color="Black",
            sku="REFUND-WEBHOOK-V",
            stock_qty=4,
            reserved_qty=0,
        )
        db.add_all([customer, product, variant])
        db.flush()
        order = Order(
            customer_id=customer.id,
            status="refund_requested",
            payment_status="refund_pending",
            delivery_status="not_started",
            total_amount=1000,
            delivery_price=0,
            discount_amount=0,
            loyalty_points_redeemed=0,
            loyalty_discount_amount=0,
            currency="RUB",
            delivery_type="pickup",
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
                quantity=1,
                price=1000,
            )
        )
        db.add(
            InventoryMovement(
                order_id=order.id,
                variant_id=variant.id,
                kind="commit",
                quantity=1,
                stock_before=5,
                stock_after=4,
                reserved_before=1,
                reserved_after=0,
                source="refund_webhook_test",
            )
        )
        ret = ReturnRequest(
            order_id=order.id,
            customer_id=customer.id,
            reason="Webhook full refund test",
            status="refund_pending",
            provider_refund_id="refund-authoritative-1",
            refund_amount=1000,
        )
        db.add(ret)
        db.commit()
        order_id = order.id
        return_id = ret.id
        variant_id = variant.id

        def override_db():
            yield db

        async def fake_fetch_yookassa_refund(refund_id: str) -> dict:
            assert refund_id == "refund-authoritative-1"
            return {
                "id": refund_id,
                "status": "succeeded",
                "amount": {"value": "1000.00", "currency": "RUB"},
            }

        app.dependency_overrides[get_db] = override_db
        refund_webhooks_api.fetch_yookassa_refund = fake_fetch_yookassa_refund
        client = TestClient(app)

        spoofed_webhook = {
            "type": "notification",
            "event": "refund.succeeded",
            "object": {
                "id": "refund-authoritative-1",
                "status": "succeeded",
                "amount": {"value": "1.00", "currency": "USD"},
            },
        }
        first = client.post("/api/returns/webhook/yookassa", json=spoofed_webhook)
        assert first.status_code == 200, first.text
        first_payload = first.json()
        assert first_payload["return_status"] == "approved"
        assert first_payload["payment_status"] == "refunded"

        duplicate = client.post("/api/returns/webhook/yookassa", json=spoofed_webhook)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["result"]["idempotent"] is True

        db.expire_all()
        persisted_order = db.query(Order).filter(Order.id == order_id).one()
        persisted_return = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).one()
        persisted_variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).one()
        movements = (
            db.query(InventoryMovement)
            .filter(
                InventoryMovement.order_id == order_id,
                InventoryMovement.variant_id == variant_id,
                InventoryMovement.kind == "return",
            )
            .all()
        )
        notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == customer.telegram_id)
            .all()
        )

        assert persisted_order.status == "refunded"
        assert persisted_order.payment_status == "refunded"
        assert persisted_return.status == "approved"
        assert persisted_variant.stock_qty == 5
        assert len(movements) == 1
        assert movements[0].quantity == 1
        assert movements[0].stock_before == 4
        assert movements[0].stock_after == 5
        assert len(notifications) == 1
    finally:
        if client is not None:
            client.close()
        refund_webhooks_api.fetch_yookassa_refund = original_fetch
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()
