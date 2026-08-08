from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from .payments import _MAX_WEBHOOK_BYTES, _parse_webhook_payload, yookassa_webhook as payment_webhook
from .refund_webhooks import yookassa_refund_webhook as refund_webhook

router = APIRouter(prefix="/webhooks", tags=["provider-webhooks"])


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


@router.post("/yookassa")
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)):
    """Canonical Basic Auth webhook URL for all YooKassa pilot events.

    The merchant cabinet binds selected notification events to one URL. Legacy
    payment/refund-specific routes remain available for backwards compatibility;
    production configuration should point YooKassa to this endpoint.
    """
    _validate_webhook_request_headers(request)
    raw_body = await request.body()
    payload, event, _obj, _provider_id = _parse_webhook_payload(raw_body)

    if event.startswith("payment."):
        return await payment_webhook(request, db)
    if event.startswith("refund."):
        return await refund_webhook(payload, db)
    return {"ok": True, "ignored": True, "event": event}
