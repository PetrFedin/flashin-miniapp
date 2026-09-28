import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.api import refund_webhooks as refund_webhooks_api
from backend.database import engine, get_db
from backend.main import app
from backend.refund_allocation_models import ReturnRefundAllocation
from backend.services.refund_allocation import REFUND_ALLOCATION_REVIEW_DETAIL
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


def test_refund_webhook_uses_authoritative_provider_state_is_idempotent_and_inventory_neutral():
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
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            title=product.title,
            size=variant.size,
            quantity=1,
            price=1000,
        )
        db.add(order_item)
        db.flush()
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
        db.flush()
        db.add(
            ReturnRefundAllocation(
                return_request_id=ret.id,
                order_id=order.id,
                order_item_id=order_item.id,
                component_kind="item",
                component_key=f"item:{order_item.id}",
                quantity_evidence=1,
                amount_cents=100000,
                policy_version=1,
            )
        )
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
        assert first_payload["result"]["inventory_effect"] == (
            "none_financial_refund_is_not_physical_return"
        )
        assert first_payload["result"]["physical_return_required_for_stock"] is True

        duplicate = client.post("/api/returns/webhook/yookassa", json=spoofed_webhook)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["result"]["idempotent"] is True
        assert duplicate.json()["result"]["inventory_effect"] == (
            "none_financial_refund_is_not_physical_return"
        )

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
        assert persisted_variant.stock_qty == 4
        assert movements == []
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


def test_legacy_refund_webhook_missing_allocation_is_acknowledged_into_review(monkeypatch):
    order = SimpleNamespace(id=91, currency="RUB")
    ret = SimpleNamespace(
        id=92,
        order_id=91,
        refund_amount=300.0,
        provider_refund_id="refund-legacy-allocation",
        status="refund_pending",
    )
    rollbacks = []
    review_calls = []

    class _Db:
        def rollback(self):
            rollbacks.append(True)

    async def fake_fetch(refund_id):
        assert refund_id == ret.provider_refund_id
        return {
            "id": refund_id,
            "status": "succeeded",
            "amount": {"value": "300.00", "currency": "RUB"},
        }

    monkeypatch.setattr(refund_webhooks_api, "require_payment_execution", lambda: None)
    monkeypatch.setattr(refund_webhooks_api, "fetch_yookassa_refund", fake_fetch)
    monkeypatch.setattr(
        refund_webhooks_api,
        "lock_return_request_for_provider_refund",
        lambda _db, _refund_id: (order, ret),
    )
    monkeypatch.setattr(
        refund_webhooks_api,
        "apply_provider_refund_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=409,
                detail=REFUND_ALLOCATION_REVIEW_DETAIL,
            )
        ),
    )
    monkeypatch.setattr(
        refund_webhooks_api,
        "mark_refund_review_required",
        lambda _db, *, return_id, order_id: (
            review_calls.append((return_id, order_id)) or True
        ),
    )

    result = asyncio.run(
        refund_webhooks_api._process_refund_webhook(
            {
                "event": "refund.succeeded",
                "object": {"id": ret.provider_refund_id},
            },
            _Db(),
        )
    )

    assert result == {
        "ok": True,
        "refund_id": ret.provider_refund_id,
        "return_id": ret.id,
        "order_id": order.id,
        "review_required": True,
        "review_code": "financial_allocation_evidence",
    }
    assert rollbacks == [True]
    assert review_calls == [(ret.id, order.id)]
