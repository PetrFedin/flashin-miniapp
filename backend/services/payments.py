import uuid

import httpx
from fastapi import HTTPException

from ..config import get_settings


def _payment_idempotence_key(order_id: int, attempt: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"flashin:yookassa:payment:{order_id}:{attempt}"))


def _refund_idempotence_key(payment_id: str, order_id: int, amount: float, currency: str) -> str:
    normalized_amount = f"{amount:.2f}"
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"flashin:yookassa:refund:{payment_id}:{order_id}:{normalized_amount}:{currency}",
        )
    )


async def create_yookassa_payment(order_id: int, amount: float, currency: str, attempt: int = 1) -> dict:
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise HTTPException(status_code=500, detail="YooKassa is not configured.")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be positive.")
    if attempt < 1:
        raise HTTPException(status_code=500, detail="Invalid payment attempt.")

    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": currency},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": f"{settings.yookassa_return_url}?order_id={order_id}"},
        "description": f"FLASHIN order #{order_id}",
        "metadata": {"order_id": str(order_id), "attempt": str(attempt)},
    }
    headers = {"Idempotence-Key": _payment_idempotence_key(order_id, attempt)}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.yookassa.ru/v3/payments",
            json=payload,
            headers=headers,
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail={"provider": "yookassa", "response": response.text})
    data = response.json()
    return {
        "provider_payment_id": data["id"],
        "status": data["status"],
        "confirmation_url": data.get("confirmation", {}).get("confirmation_url", ""),
    }


async def fetch_yookassa_payment(payment_id: str) -> dict:
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise HTTPException(status_code=500, detail="YooKassa is not configured.")
    if not payment_id:
        raise HTTPException(status_code=400, detail="Payment id is required.")

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"https://api.yookassa.ru/v3/payments/{payment_id}",
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail={"provider": "yookassa", "response": response.text})
    return response.json()


async def create_yookassa_refund(payment_id: str, amount: float, currency: str, order_id: int) -> dict:
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise HTTPException(status_code=500, detail="YooKassa is not configured.")
    if not payment_id:
        raise HTTPException(status_code=400, detail="Payment id is required.")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Refund amount must be positive.")

    payload = {
        "payment_id": payment_id,
        "amount": {"value": f"{amount:.2f}", "currency": currency},
        "description": f"FLASHIN refund for order #{order_id}",
    }
    headers = {"Idempotence-Key": _refund_idempotence_key(payment_id, order_id, amount, currency)}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.yookassa.ru/v3/refunds",
            json=payload,
            headers=headers,
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail={"provider": "yookassa", "response": response.text})
    data = response.json()
    return {"refund_id": data["id"], "status": data["status"]}
