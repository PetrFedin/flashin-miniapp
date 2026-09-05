#!/usr/bin/env python3
"""Test-only ASGI wrapper for browser -> API -> PostgreSQL integration coverage.

The production application remains unchanged. This module is allowed to boot only
under the explicit integrated-E2E test contract and replaces only external
provider network boundaries with deterministic local endpoints. FLASHIN auth,
checkout, payment/refund persistence, unified YooKassa webhook processing,
inventory, loyalty, fulfillment, notifications, and provider-command outboxes
run through the real backend and the real PostgreSQL schema.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse


if os.getenv("APP_ENV", "").strip().lower() not in {"test", "ci"}:
    raise RuntimeError("Integrated E2E app is forbidden outside test/ci")
if os.getenv("INTEGRATED_E2E", "").strip().lower() not in {"1", "true", "yes"}:
    raise RuntimeError("Integrated E2E app requires INTEGRATED_E2E=true")

from backend.services import payments as payment_service  # noqa: E402

_PROVIDER_BASE = "http://127.0.0.1:8000/__e2e/yookassa/v3"
_APP_BASE = "http://127.0.0.1:8000"
payment_service._YOOKASSA_API = _PROVIDER_BASE

from backend.database import SessionLocal  # noqa: E402
from backend.models import (  # noqa: E402
    FulfillmentTask,
    InventoryMovement,
    Notification,
    Order,
    Payment,
    ProductVariant,
    ReturnRequest,
)
from backend.provider_models import ProviderCommand  # noqa: E402
from backend.main import app  # noqa: E402

_payments: dict[str, dict[str, Any]] = {}
_refunds: dict[str, dict[str, Any]] = {}


def _payload_object(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="E2E provider payload must be an object")
    return payload


def _provider_public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


async def _deliver_yookassa_webhook(event: str, object_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Deliver every callback twice to prove idempotency at the real webhook boundary."""
    payload = {"type": "notification", "event": event, "object": object_payload}
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for _ in range(2):
            response = await client.post(
                f"{_APP_BASE}/api/webhooks/yookassa",
                json=payload,
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Integrated YooKassa webhook failed: HTTP {response.status_code}",
                )
            body = response.json()
            results.append(body if isinstance(body, dict) else {"raw": body})
    return results


@app.post("/__e2e/yookassa/v3/payments", include_in_schema=False)
async def e2e_create_payment(request: Request):
    payload = _payload_object(await request.json())
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    order_id = str(metadata.get("order_id") or "").strip()
    if not order_id.isdigit():
        raise HTTPException(status_code=400, detail="E2E payment requires order_id metadata")

    payment_id = f"e2e-payment-{order_id}-{len(_payments) + 1}"
    input_confirmation = (
        payload.get("confirmation") if isinstance(payload.get("confirmation"), dict) else {}
    )
    row = {
        "id": payment_id,
        "status": "pending",
        "amount": payload.get("amount") or {},
        "metadata": metadata,
        "confirmation": {
            "type": "redirect",
            "confirmation_url": f"{_APP_BASE}/__e2e/yookassa/confirm-payment/{payment_id}",
        },
        "_return_url": str(input_confirmation.get("return_url") or "http://127.0.0.1:5173/payment-result"),
    }
    _payments[payment_id] = row
    return _provider_public_row(row)


@app.get("/__e2e/yookassa/v3/payments/{payment_id}", include_in_schema=False)
async def e2e_get_payment(payment_id: str):
    row = _payments.get(payment_id)
    if not row:
        raise HTTPException(status_code=404, detail="E2E payment not found")
    return _provider_public_row(row)


@app.get("/__e2e/yookassa/confirm-payment/{payment_id}", include_in_schema=False)
async def e2e_confirm_payment(payment_id: str):
    row = _payments.get(payment_id)
    if not row:
        raise HTTPException(status_code=404, detail="E2E payment not found")
    row["status"] = "succeeded"
    await _deliver_yookassa_webhook(
        "payment.succeeded",
        {"id": payment_id, "status": "succeeded"},
    )
    return RedirectResponse(str(row["_return_url"]), status_code=303)


@app.post("/__e2e/yookassa/v3/refunds", include_in_schema=False)
async def e2e_create_refund(request: Request):
    payload = _payload_object(await request.json())
    payment_id = str(payload.get("payment_id") or "").strip()
    if payment_id not in _payments:
        raise HTTPException(status_code=404, detail="E2E payment not found")
    refund_id = f"e2e-refund-{len(_refunds) + 1}"
    row = {
        "id": refund_id,
        "status": "pending",
        "payment_id": payment_id,
        "amount": payload.get("amount") or {},
    }
    _refunds[refund_id] = row
    return row


@app.get("/__e2e/yookassa/v3/refunds/{refund_id}", include_in_schema=False)
async def e2e_get_refund(refund_id: str):
    row = _refunds.get(refund_id)
    if not row:
        raise HTTPException(status_code=404, detail="E2E refund not found")
    return row


@app.post("/__e2e/yookassa/confirm-refund/{refund_id}", include_in_schema=False)
async def e2e_confirm_refund(refund_id: str):
    row = _refunds.get(refund_id)
    if not row:
        raise HTTPException(status_code=404, detail="E2E refund not found")
    row["status"] = "succeeded"
    webhooks = await _deliver_yookassa_webhook(
        "refund.succeeded",
        {"id": refund_id, "status": "succeeded"},
    )
    return {"ok": True, "refund_id": refund_id, "webhooks": webhooks}


@app.get("/__e2e/state/orders/{order_id}", include_in_schema=False)
def e2e_order_state(order_id: int):
    """Safe test-only persistence snapshot used by Playwright assertions."""
    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        variant_ids = sorted({item.variant_id for item in order.items})
        variants = (
            db.query(ProductVariant)
            .filter(ProductVariant.id.in_(variant_ids))
            .order_by(ProductVariant.id.asc())
            .all()
            if variant_ids
            else []
        )
        task = db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order.id).first()
        returns = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.order_id == order.id)
            .order_by(ReturnRequest.id.asc())
            .all()
        )
        return_ids = {str(row.id) for row in returns}
        commands = db.query(ProviderCommand).order_by(ProviderCommand.id.asc()).all()
        relevant_commands = [
            command
            for command in commands
            if (
                command.aggregate_type == "order" and str(command.aggregate_id) == str(order.id)
            )
            or (
                command.aggregate_type == "return" and str(command.aggregate_id) in return_ids
            )
        ]
        notifications = (
            db.query(Notification)
            .filter(Notification.telegram_id == str(order.customer.telegram_id))
            .order_by(Notification.id.asc())
            .all()
        )
        payments = (
            db.query(Payment)
            .filter(Payment.order_id == order.id)
            .order_by(Payment.id.asc())
            .all()
        )
        movements = (
            db.query(InventoryMovement)
            .filter(InventoryMovement.order_id == order.id)
            .order_by(InventoryMovement.id.asc())
            .all()
        )

        return {
            "order": {
                "id": order.id,
                "status": order.status,
                "payment_status": order.payment_status,
                "delivery_status": order.delivery_status,
                "tracking_number": order.tracking_number,
                "total_amount": order.total_amount,
                "currency": order.currency,
            },
            "variants": [
                {
                    "id": row.id,
                    "sku": row.sku,
                    "stock_qty": row.stock_qty,
                    "reserved_qty": row.reserved_qty,
                    "available_qty": row.available_qty,
                }
                for row in variants
            ],
            "fulfillment": None
            if not task
            else {"id": task.id, "status": task.status, "order_id": task.order_id},
            "payments": [
                {
                    "id": row.id,
                    "provider_payment_id": row.provider_payment_id,
                    "status": row.status,
                    "amount": row.amount,
                }
                for row in payments
            ],
            "returns": [
                {
                    "id": row.id,
                    "status": row.status,
                    "provider_refund_id": row.provider_refund_id,
                    "refund_amount": row.refund_amount,
                }
                for row in returns
            ],
            "inventory_movements": [
                {
                    "id": row.id,
                    "variant_id": row.variant_id,
                    "kind": row.kind,
                    "quantity": row.quantity,
                    "stock_before": row.stock_before,
                    "stock_after": row.stock_after,
                    "reserved_before": row.reserved_before,
                    "reserved_after": row.reserved_after,
                }
                for row in movements
            ],
            "provider_commands": [
                {
                    "id": row.id,
                    "provider": row.provider,
                    "command_type": row.command_type,
                    "status": row.status,
                    "aggregate_type": row.aggregate_type,
                    "aggregate_id": row.aggregate_id,
                }
                for row in relevant_commands
            ],
            "notifications": [
                {"id": row.id, "status": row.status, "message": row.message}
                for row in notifications
            ],
        }
    finally:
        db.close()
