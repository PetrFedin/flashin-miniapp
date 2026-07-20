from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..telegram_commerce_models import TelegramPurchase
from .telegram_commerce import _fulfill_purchase

router = APIRouter(prefix="/telegram/webhook", tags=["telegram"])
settings = get_settings()


def _bot_api(method: str, payload: dict[str, Any]) -> Any:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    try:
        response = httpx.post(url, json=payload, timeout=15.0)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Telegram API unavailable: {exc}") from exc
    if not body.get("ok"):
        raise HTTPException(status_code=502, detail=body.get("description", "Telegram API error"))
    return body.get("result")


def _validate_secret(value: str | None) -> None:
    configured = settings.telegram_webhook_secret
    if not configured or not secrets.compare_digest(value or "", configured):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")


@router.post("")
def telegram_webhook(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    _validate_secret(x_telegram_bot_api_secret_token)

    pre_checkout = update.get("pre_checkout_query")
    if pre_checkout:
        query_id = pre_checkout.get("id")
        invoice_payload = pre_checkout.get("invoice_payload", "")
        currency = pre_checkout.get("currency")
        total_amount = pre_checkout.get("total_amount")

        purchase = db.query(TelegramPurchase).filter(
            TelegramPurchase.invoice_payload == invoice_payload
        ).first()
        valid = bool(
            purchase
            and purchase.status in {"invoice_created", "pending", "created"}
            and currency == "XTR"
            and total_amount == purchase.stars_amount
        )
        _bot_api(
            "answerPreCheckoutQuery",
            {
                "pre_checkout_query_id": query_id,
                "ok": valid,
                **({} if valid else {"error_message": "Платеж не прошел проверку. Обновите Mini App и повторите."}),
            },
        )
        return {"ok": True, "handled": "pre_checkout_query", "accepted": valid}

    message = update.get("message") or update.get("edited_message") or {}
    successful_payment = message.get("successful_payment")
    if successful_payment:
        invoice_payload = successful_payment.get("invoice_payload", "")
        purchase = db.query(TelegramPurchase).filter(
            TelegramPurchase.invoice_payload == invoice_payload
        ).first()
        if not purchase:
            raise HTTPException(status_code=404, detail="Telegram purchase not found")

        if purchase.status == "paid":
            return {"ok": True, "handled": "successful_payment", "idempotent": True}

        if successful_payment.get("currency") != "XTR":
            raise HTTPException(status_code=409, detail="Unexpected payment currency")
        if successful_payment.get("total_amount") != purchase.stars_amount:
            raise HTTPException(status_code=409, detail="Stars amount mismatch")

        telegram_payment_charge_id = successful_payment.get("telegram_payment_charge_id")
        if not isinstance(telegram_payment_charge_id, str) or not telegram_payment_charge_id:
            raise HTTPException(status_code=409, detail="Telegram payment charge ID is missing")

        purchase.status = "paid"
        purchase.telegram_payment_charge_id = telegram_payment_charge_id
        purchase.provider_payment_charge_id = successful_payment.get(
            "provider_payment_charge_id", ""
        )
        purchase.paid_at = datetime.utcnow()
        _fulfill_purchase(db, purchase)
        db.commit()
        return {"ok": True, "handled": "successful_payment", "idempotent": False}

    return {"ok": True, "handled": "ignored"}
