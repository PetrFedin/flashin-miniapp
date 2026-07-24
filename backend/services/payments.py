import hashlib

import httpx
from fastapi import HTTPException

from ..config import get_settings


def _idempotency_key(operation: str, order_id: int, amount: float, currency: str, external_id: str = "") -> str:
    raw = f"flashin:{operation}:{order_id}:{amount:.2f}:{currency}:{external_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_yookassa_payment(order_id: int, amount: float, currency: str) -> dict:
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise HTTPException(status_code=500, detail="YooKassa is not configured.")
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Payment amount must be positive")

    normalized_currency = currency.strip().upper()
    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": normalized_currency},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"{settings.yookassa_return_url}?order_id={order_id}",
        },
        "description": f"FLASHIN order #{order_id}",
        "metadata": {"order_id": str(order_id)},
    }
    headers = {
        "Idempotence-Key": _idempotency_key("payment", order_id, amount, normalized_currency),
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.yookassa.ru/v3/payments",
                json=payload,
                headers=headers,
                auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="YooKassa request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="YooKassa is temporarily unavailable") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail={"provider": "yookassa", "response": response.text})

    data = response.json()
    payment_id = data.get("id")
    status = data.get("status")
    if not payment_id or not status:
        raise HTTPException(status_code=502, detail="Invalid payment provider response")

    return {
        "provider_payment_id": payment_id,
        "status": status,
        "confirmation_url": data.get("confirmation", {}).get("confirmation_url", ""),
    }


async def fetch_yookassa_payment(payment_id: str) -> dict:
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise HTTPException(status_code=500, detail="YooKassa is not configured.")
    if not payment_id.strip():
        raise HTTPException(status_code=422, detail="Payment id is required")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"https://api.yookassa.ru/v3/payments/{payment_id}",
                auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="YooKassa request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="YooKassa is temporarily unavailable") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail={"provider": "yookassa", "response": response.text})
    return response.json()


async def create_yookassa_refund(payment_id: str, amount: float, currency: str, order_id: int) -> dict:
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise HTTPException(status_code=500, detail="YooKassa is not configured.")
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Refund amount must be positive")

    normalized_currency = currency.strip().upper()
    normalized_payment_id = payment_id.strip()
    if not normalized_payment_id:
        raise HTTPException(status_code=422, detail="Payment id is required")

    payload = {
        "payment_id": normalized_payment_id,
        "amount": {"value": f"{amount:.2f}", "currency": normalized_currency},
        "description": f"FLASHIN refund for order #{order_id}",
    }
    headers = {
        "Idempotence-Key": _idempotency_key(
            "refund", order_id, amount, normalized_currency, normalized_payment_id
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.yookassa.ru/v3/refunds",
                json=payload,
                headers=headers,
                auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="YooKassa request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="YooKassa is temporarily unavailable") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail={"provider": "yookassa", "response": response.text})

    data = response.json()
    refund_id = data.get("id")
    status = data.get("status")
    if not refund_id or not status:
        raise HTTPException(status_code=502, detail="Invalid refund provider response")
    return {"refund_id": refund_id, "status": status}
