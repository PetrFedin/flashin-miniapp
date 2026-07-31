import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import WebhookDestination, WebhookOutbox
from ..services.outbox import schedule_retry
from ..services.webhook_security import (
    is_internal_destination,
    resolve_public_webhook_addresses,
)

_OUTBOX_BATCH_SIZE = 50
_OUTBOX_LEASE_MINUTES = 5
_MAX_WEBHOOK_BODY_BYTES = 256 * 1024


def _claim_outbox(db: Session) -> list[dict]:
    now = utcnow_naive()
    rows = (
        db.query(WebhookOutbox)
        .filter(
            or_(
                (
                    (WebhookOutbox.status == "pending")
                    & (
                        WebhookOutbox.next_attempt_at.is_(None)
                        | (WebhookOutbox.next_attempt_at <= now)
                    )
                ),
                (
                    (WebhookOutbox.status == "processing")
                    & (WebhookOutbox.next_attempt_at <= now)
                ),
            )
        )
        .order_by(WebhookOutbox.id.asc())
        .with_for_update(skip_locked=True)
        .limit(_OUTBOX_BATCH_SIZE)
        .all()
    )
    claimed: list[dict] = []
    lease_until = now + timedelta(minutes=_OUTBOX_LEASE_MINUTES)
    for row in rows:
        row.status = "processing"
        row.next_attempt_at = lease_until
        claimed.append(
            {
                "id": row.id,
                "destination": row.destination,
                "event_type": row.event_type,
                "payload": row.payload,
            }
        )
    db.commit()
    return claimed


def _finish_outbox(
    db: Session,
    row_id: int,
    *,
    success: bool,
    destination: str = "",
    error: str = "",
) -> bool:
    row = (
        db.query(WebhookOutbox)
        .filter(WebhookOutbox.id == row_id, WebhookOutbox.status == "processing")
        .with_for_update()
        .first()
    )
    if not row:
        db.rollback()
        return False

    if success:
        if destination:
            row.destination = destination
        row.status = "sent"
        row.last_error = ""
        row.next_attempt_at = None
    else:
        schedule_retry(row, error)
    db.commit()
    return True


async def process_outbox(db: Session) -> int:
    claimed = _claim_outbox(db)
    if not claimed:
        return 0

    sent = 0
    settings = get_settings()
    timeout = httpx.Timeout(connect=5, read=10, write=10, pool=5)
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
    ) as client:
        for item in claimed:
            row_id = item["id"]
            raw_destination = item["destination"]
            if is_internal_destination(raw_destination):
                if _finish_outbox(db, row_id, success=True):
                    sent += 1
                continue

            normalized_destination = ""
            try:
                normalized_destination, _ = await asyncio.to_thread(
                    resolve_public_webhook_addresses,
                    raw_destination,
                )
                payload = json.loads(item["payload"])
                if not isinstance(payload, (dict, list)):
                    raise ValueError("Webhook payload must be a JSON object or array")
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if len(body) > _MAX_WEBHOOK_BODY_BYTES:
                    raise ValueError("Webhook payload is too large")

                destination_configs = (
                    db.query(WebhookDestination)
                    .filter(
                        WebhookDestination.url == normalized_destination,
                        WebhookDestination.active.is_(True),
                        WebhookDestination.event_type.in_([item["event_type"], "*"]),
                    )
                    .order_by(WebhookDestination.id.asc())
                    .all()
                )
                exact_config = next(
                    (
                        config
                        for config in destination_configs
                        if config.event_type == item["event_type"]
                    ),
                    None,
                )
                destination_config = exact_config or (
                    destination_configs[0] if destination_configs else None
                )
                signing_secret = (
                    destination_config.signing_secret
                    if destination_config and destination_config.signing_secret
                    else settings.outbox_signing_secret
                )
                if not signing_secret or len(signing_secret) < 32:
                    raise RuntimeError("Webhook signing secret is not configured securely")

                signature = hmac.new(
                    signing_secret.encode("utf-8"),
                    body,
                    hashlib.sha256,
                ).hexdigest()
                request = client.build_request(
                    "POST",
                    normalized_destination,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "FLASHIN-Outbox/1.0",
                        "X-Flashin-Signature": signature,
                        "X-Flashin-Event-Id": str(row_id),
                        "X-Flashin-Event-Type": item["event_type"],
                    },
                )
                response = await client.send(request, stream=True)
                try:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise RuntimeError(f"Webhook returned HTTP {response.status_code}")
                finally:
                    await response.aclose()

                if _finish_outbox(
                    db,
                    row_id,
                    success=True,
                    destination=normalized_destination,
                ):
                    sent += 1
            except Exception as exc:
                _finish_outbox(
                    db,
                    row_id,
                    success=False,
                    error=f"{exc.__class__.__name__}: {exc}"[:2000],
                )

    return sent
