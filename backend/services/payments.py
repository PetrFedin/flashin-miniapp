import asyncio
import math
import uuid
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from ..config import get_settings

_YOOKASSA_API = "https://api.yookassa.ru/v3"
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


def _payment_idempotence_key(order_id: int, attempt: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"flashin:yookassa:payment:{order_id}:{attempt}"))


def _refund_idempotence_key(
    payment_id: str,
    order_id: int,
    refund_request_id: int,
    amount: float,
    currency: str,
) -> str:
    normalized_amount = f"{amount:.2f}"
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "flashin:yookassa:refund:"
                f"{payment_id}:{order_id}:{refund_request_id}:{normalized_amount}:{currency}"
            ),
        )
    )


def _validate_positive_amount(amount: float, operation: str) -> float:
    try:
        normalized = float(amount)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{operation} amount must be numeric.")
    if not math.isfinite(normalized) or normalized <= 0:
        raise HTTPException(status_code=400, detail=f"{operation} amount must be positive.")
    return normalized


def _normalize_currency(currency: str) -> str:
    normalized = str(currency or "").strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise HTTPException(status_code=400, detail="Currency must be a three-letter code.")
    return normalized


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        raw_retry_after = response.headers.get("Retry-After", "")
        try:
            return min(max(float(raw_retry_after), 0.0), 5.0)
        except (TypeError, ValueError):
            pass
    return min(0.25 * (2**attempt), 2.0)


async def _request_yookassa(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    idempotence_key: str | None = None,
) -> dict:
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise HTTPException(status_code=500, detail="YooKassa is not configured.")

    headers = {}
    if idempotence_key:
        headers["Idempotence-Key"] = idempotence_key

    timeout = httpx.Timeout(20.0, connect=5.0)
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        for attempt in range(_MAX_ATTEMPTS):
            response: httpx.Response | None = None
            try:
                response = await client.request(
                    method,
                    f"{_YOOKASSA_API}{path}",
                    json=payload,
                    headers=headers,
                    auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
                )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt + 1 < _MAX_ATTEMPTS:
                    await asyncio.sleep(_retry_delay(None, attempt))
                    continue
                raise HTTPException(
                    status_code=502,
                    detail={"provider": "yookassa", "error": "network_error"},
                ) from exc

            if response.status_code in _TRANSIENT_STATUSES and attempt + 1 < _MAX_ATTEMPTS:
                await asyncio.sleep(_retry_delay(response, attempt))
                continue

            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "provider": "yookassa",
                        "status_code": response.status_code,
                        "error": "provider_rejected_request",
                    },
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=502,
                    detail={"provider": "yookassa", "error": "invalid_json"},
                ) from exc
            if not isinstance(data, dict):
                raise HTTPException(
                    status_code=502,
                    detail={"provider": "yookassa", "error": "invalid_payload"},
                )
            return data

    raise HTTPException(
        status_code=502,
        detail={"provider": "yookassa", "error": last_error.__class__.__name__ if last_error else "unknown"},
    )


async def create_yookassa_payment(order_id: int, amount: float, currency: str, attempt: int = 1) -> dict:
    normalized_amount = _validate_positive_amount(amount, "Payment")
    normalized_currency = _normalize_currency(currency)
    if attempt < 1:
        raise HTTPException(status_code=500, detail="Invalid payment attempt.")

    payload = {
        "amount": {"value": f"{normalized_amount:.2f}", "currency": normalized_currency},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"{get_settings().yookassa_return_url}?order_id={order_id}",
        },
        "description": f"FLASHIN order #{order_id}",
        "metadata": {"order_id": str(order_id), "attempt": str(attempt)},
    }
    data = await _request_yookassa(
        "POST",
        "/payments",
        payload=payload,
        idempotence_key=_payment_idempotence_key(order_id, attempt),
    )
    return {
        "provider_payment_id": data["id"],
        "status": data["status"],
        "confirmation_url": data.get("confirmation", {}).get("confirmation_url", ""),
    }


async def fetch_yookassa_payment(payment_id: str) -> dict:
    normalized_payment_id = str(payment_id or "").strip()
    if not normalized_payment_id:
        raise HTTPException(status_code=400, detail="Payment id is required.")
    return await _request_yookassa("GET", f"/payments/{quote(normalized_payment_id, safe='')}")


async def create_yookassa_refund(
    payment_id: str,
    amount: float,
    currency: str,
    order_id: int,
    refund_request_id: int,
) -> dict:
    normalized_payment_id = str(payment_id or "").strip()
    if not normalized_payment_id:
        raise HTTPException(status_code=400, detail="Payment id is required.")
    if order_id <= 0 or refund_request_id <= 0:
        raise HTTPException(status_code=400, detail="Order and return request ids are required.")
    normalized_amount = _validate_positive_amount(amount, "Refund")
    normalized_currency = _normalize_currency(currency)

    payload = {
        "payment_id": normalized_payment_id,
        "amount": {"value": f"{normalized_amount:.2f}", "currency": normalized_currency},
        "description": f"FLASHIN refund #{refund_request_id} for order #{order_id}",
    }
    data = await _request_yookassa(
        "POST",
        "/refunds",
        payload=payload,
        idempotence_key=_refund_idempotence_key(
            normalized_payment_id,
            order_id,
            refund_request_id,
            normalized_amount,
            normalized_currency,
        ),
    )
    return {
        "refund_id": data["id"],
        "status": data["status"],
        "amount": data.get("amount", {}),
    }


async def fetch_yookassa_refund(refund_id: str) -> dict:
    normalized_refund_id = str(refund_id or "").strip()
    if not normalized_refund_id:
        raise HTTPException(status_code=400, detail="Refund id is required.")
    return await _request_yookassa("GET", f"/refunds/{quote(normalized_refund_id, safe='')}")
