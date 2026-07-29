import asyncio
import math
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from ..config import get_settings
from .http_clients import yookassa_client

_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_MONEY_STEP = Decimal("0.01")
_PAYMENT_STATUSES = frozenset({"pending", "waiting_for_capture", "succeeded", "canceled"})
_REFUND_STATUSES = frozenset({"pending", "succeeded", "canceled"})


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


def _provider_identifier(value: object, operation: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": f"invalid_{operation.lower()}_id"},
        )
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": f"invalid_{operation.lower()}_id"},
        )
    return normalized


def _provider_status(value: object, allowed: frozenset[str], operation: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": f"invalid_{operation.lower()}_status"},
        )
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": f"invalid_{operation.lower()}_status"},
        )
    return normalized


def _validate_provider_amount(
    value: object,
    expected_amount: float,
    expected_currency: str,
    operation: str,
) -> dict:
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": f"invalid_{operation.lower()}_amount"},
        )

    try:
        provider_amount = Decimal(str(value.get("value"))).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
        local_amount = Decimal(str(expected_amount)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": f"invalid_{operation.lower()}_amount"},
        )

    provider_currency = str(value.get("currency") or "").strip().upper()
    if (
        not provider_amount.is_finite()
        or provider_amount <= 0
        or provider_amount != local_amount
        or provider_currency != expected_currency
    ):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": f"{operation.lower()}_amount_mismatch"},
        )
    return {"value": f"{provider_amount:.2f}", "currency": provider_currency}


def _confirmation_url(data: dict) -> str:
    confirmation = data.get("confirmation")
    if confirmation is None:
        return ""
    if not isinstance(confirmation, dict):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": "invalid_confirmation"},
        )
    value = confirmation.get("confirmation_url", "")
    if not isinstance(value, str) or len(value) > 2048:
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": "invalid_confirmation"},
        )
    return value.strip()


def validate_yookassa_refund(
    data: dict,
    payment_id: str,
    amount: float,
    currency: str,
    *,
    expected_refund_id: str | None = None,
) -> dict:
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": "invalid_refund_payload"},
        )

    normalized_payment_id = _provider_identifier(payment_id, "Payment")
    normalized_amount = _validate_positive_amount(amount, "Refund")
    normalized_currency = _normalize_currency(currency)
    refund_id = _provider_identifier(data.get("id"), "Refund")
    if expected_refund_id is not None and refund_id != _provider_identifier(expected_refund_id, "Refund"):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": "refund_id_mismatch"},
        )

    status = _provider_status(data.get("status"), _REFUND_STATUSES, "Refund")
    returned_payment_id = _provider_identifier(data.get("payment_id"), "Payment")
    if returned_payment_id != normalized_payment_id:
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": "refund_payment_mismatch"},
        )
    amount_contract = _validate_provider_amount(
        data.get("amount"),
        normalized_amount,
        normalized_currency,
        "Refund",
    )
    return {
        "refund_id": refund_id,
        "payment_id": returned_payment_id,
        "status": status,
        "amount": amount_contract,
    }


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
    last_error: Exception | None = None

    async with yookassa_client() as client:
        for attempt in range(_MAX_ATTEMPTS):
            response: httpx.Response | None = None
            try:
                response = await client.request(
                    method,
                    path,
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
    payment_id = _provider_identifier(data.get("id"), "Payment")
    status = _provider_status(data.get("status"), _PAYMENT_STATUSES, "Payment")
    _validate_provider_amount(data.get("amount"), normalized_amount, normalized_currency, "Payment")
    return {
        "provider_payment_id": payment_id,
        "status": status,
        "confirmation_url": _confirmation_url(data),
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
    return validate_yookassa_refund(
        data,
        normalized_payment_id,
        normalized_amount,
        normalized_currency,
    )


async def fetch_yookassa_refund(refund_id: str) -> dict:
    normalized_refund_id = str(refund_id or "").strip()
    if not normalized_refund_id:
        raise HTTPException(status_code=400, detail="Refund id is required.")
    return await _request_yookassa("GET", f"/refunds/{quote(normalized_refund_id, safe='')}")
