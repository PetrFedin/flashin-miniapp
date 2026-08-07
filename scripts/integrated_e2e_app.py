#!/usr/bin/env python3
"""Test-only ASGI wrapper for browser -> API -> PostgreSQL integration coverage.

The production application remains unchanged. This module is allowed to boot only
under the explicit integrated-E2E test contract and replaces only the external
YooKassa network boundary with local deterministic endpoints. All FLASHIN API,
checkout, payment persistence, inventory, loyalty, event, and fulfillment logic
runs through the real backend.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request


if os.getenv("APP_ENV", "").strip().lower() not in {"test", "ci"}:
    raise RuntimeError("Integrated E2E app is forbidden outside test/ci")
if os.getenv("INTEGRATED_E2E", "").strip().lower() not in {"1", "true", "yes"}:
    raise RuntimeError("Integrated E2E app requires INTEGRATED_E2E=true")

from backend.services import payments as payment_service  # noqa: E402

_PROVIDER_BASE = "http://127.0.0.1:8000/__e2e/yookassa/v3"
payment_service._YOOKASSA_API = _PROVIDER_BASE

from backend.main import app  # noqa: E402

_payments: dict[str, dict[str, Any]] = {}
_refunds: dict[str, dict[str, Any]] = {}


def _payload_object(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="E2E provider payload must be an object")
    return payload


@app.post("/__e2e/yookassa/v3/payments", include_in_schema=False)
async def e2e_create_payment(request: Request):
    payload = _payload_object(await request.json())
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    order_id = str(metadata.get("order_id") or "").strip()
    if not order_id.isdigit():
        raise HTTPException(status_code=400, detail="E2E payment requires order_id metadata")
    payment_id = f"e2e-payment-{order_id}-{len(_payments) + 1}"
    row = {
        "id": payment_id,
        "status": "succeeded",
        "amount": payload.get("amount") or {},
        "metadata": metadata,
        "confirmation": {
            "type": "redirect",
            "confirmation_url": f"http://127.0.0.1:5173/payment-result?order_id={order_id}",
        },
    }
    _payments[payment_id] = row
    return row


@app.get("/__e2e/yookassa/v3/payments/{payment_id}", include_in_schema=False)
async def e2e_get_payment(payment_id: str):
    row = _payments.get(payment_id)
    if not row:
        raise HTTPException(status_code=404, detail="E2E payment not found")
    return row


@app.post("/__e2e/yookassa/v3/refunds", include_in_schema=False)
async def e2e_create_refund(request: Request):
    payload = _payload_object(await request.json())
    payment_id = str(payload.get("payment_id") or "").strip()
    if payment_id not in _payments:
        raise HTTPException(status_code=404, detail="E2E payment not found")
    refund_id = f"e2e-refund-{len(_refunds) + 1}"
    row = {
        "id": refund_id,
        "status": "succeeded",
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
