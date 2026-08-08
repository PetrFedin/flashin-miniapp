from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Order, ReturnRequest
from ..services.payments import fetch_yookassa_refund
from ..services.pilot_circuit_breaker import PilotCircuitBreakerError, trip_pilot_circuit_breaker
from ..services.refund_state import (
    apply_provider_refund_status,
    provider_refund_amount,
    refund_money,
)
from .payments import (
    _MAX_WEBHOOK_BYTES,
    _parse_webhook_payload,
    yookassa_webhook as payment_webhook,
)

router = APIRouter(tags=["returns-webhook"])


def _trip_after_rollback(order_id: int, reason: str, original: HTTPException) -> None:
    try:
        trip_pilot_circuit_breaker(order_id=order_id, reason=reason)
    except PilotCircuitBreakerError as exc:
        raise HTTPException(
            status_code=503,
            detail="Refund webhook integrity failed and pilot safety stop could not be persisted",
        ) from exc
    raise original


def _validate_webhook_request_headers(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type and content_type != "application/json":
        raise HTTPException(status_code=415, detail="Webhook content type must be application/json")

    raw_content_length = request.headers.get("content-length", "").strip()
    if not raw_content_length:
        return
    try:
        content_length = int(raw_content_length)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header") from exc
    if content_length > _MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")


async def _process_refund_webhook(payload: dict, db: Session):
    event = str(payload.get("event") or "").strip().lower()
    if event != "refund.succeeded":
        return {"ok": True, "ignored": True, "event": event}

    raw_object = payload.get("object") or {}
    if not isinstance(raw_object, dict):
        raise HTTPException(status_code=400, detail="Invalid YooKassa refund webhook object")
    refund_id = str(raw_object.get("id") or "").strip()
    if not refund_id:
        raise HTTPException(status_code=400, detail="Refund id is required")

    authoritative = await fetch_yookassa_refund(refund_id)
    authoritative_id = str(authoritative.get("id") or "").strip()
    authoritative_status = str(authoritative.get("status") or "").strip().lower()
    if authoritative_id != refund_id:
        raise HTTPException(status_code=409, detail="Provider refund id mismatch")
    if authoritative_status != "succeeded":
        raise HTTPException(status_code=409, detail="Provider refund is not succeeded")

    try:
        ret = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.provider_refund_id == refund_id)
            .with_for_update()
            .first()
        )
        if not ret:
            raise HTTPException(status_code=409, detail="Provider refund is not bound to a return request")
        order = db.query(Order).filter(Order.id == ret.order_id).with_for_update().first()
        if not order:
            raise HTTPException(status_code=409, detail="Refund order is missing")

        actual_amount = provider_refund_amount(authoritative, order.currency)
        expected_amount = refund_money(ret.refund_amount, "stored refund amount")
        if actual_amount != expected_amount:
            order_id = order.id
            raise HTTPException(status_code=409, detail="Provider refund amount does not match stored refund")

        result = apply_provider_refund_status(db, ret, order, authoritative_status)
        db.commit()
        return {
            "ok": True,
            "refund_id": refund_id,
            "return_id": ret.id,
            "order_id": order.id,
            "return_status": ret.status,
            "order_status": order.status,
            "payment_status": order.payment_status,
            "result": result,
        }
    except HTTPException as exc:
        order_id = locals().get("order_id") or getattr(locals().get("order"), "id", None)
        db.rollback()
        if order_id and exc.status_code == 409 and "amount" in str(exc.detail).lower():
            _trip_after_rollback(
                int(order_id),
                "refund_webhook_amount_mismatch",
                exc,
            )
        raise
    except IntegrityError as exc:
        order_id = getattr(locals().get("order"), "id", None)
        db.rollback()
        if order_id:
            _trip_after_rollback(
                int(order_id),
                "refund_webhook_integrity_conflict",
                HTTPException(status_code=409, detail="Refund webhook finalization conflict"),
            )
        raise HTTPException(status_code=409, detail="Refund webhook finalization conflict") from exc
    except Exception:
        db.rollback()
        raise


@router.post("/returns/webhook/yookassa")
async def yookassa_refund_webhook(payload: dict, db: Session = Depends(get_db)):
    """Legacy refund-only callback kept during the pilot migration window."""
    return await _process_refund_webhook(payload, db)


@router.post("/webhooks/yookassa")
async def yookassa_provider_webhook(request: Request, db: Session = Depends(get_db)):
    """Canonical YooKassa callback for payment and refund events.

    HTTP Basic Auth merchants configure one notification URL in the YooKassa
    cabinet and select the payment/refund events delivered to that URL. The
    handler deliberately re-fetches authoritative provider state in the
    payment/refund processors instead of trusting webhook object fields.
    """
    _validate_webhook_request_headers(request)
    raw_body = await request.body()
    payload, event, _obj, _provider_id = _parse_webhook_payload(raw_body)

    if event.startswith("payment."):
        return await payment_webhook(request, db)
    if event.startswith("refund."):
        return await _process_refund_webhook(payload, db)
    return {"ok": True, "ignored": True, "event": event}
