import hashlib
import hmac
import json
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import WebhookDestination, WebhookOutbox
from ..services.outbox import schedule_retry
from ..services.webhook_security import is_internal_destination, normalize_webhook_url


async def process_outbox(db: Session) -> int:
    rows = (
        db.query(WebhookOutbox)
        .filter(WebhookOutbox.status == "pending")
        .filter(
            (WebhookOutbox.next_attempt_at.is_(None))
            | (WebhookOutbox.next_attempt_at <= datetime.utcnow())
        )
        .order_by(WebhookOutbox.id.asc())
        .with_for_update(skip_locked=True)
        .limit(50)
        .all()
    )
    sent = 0
    settings = get_settings()

    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for row in rows:
            if is_internal_destination(row.destination):
                row.status = "sent"
                row.last_error = ""
                row.next_attempt_at = None
                sent += 1
                continue

            try:
                destination = normalize_webhook_url(row.destination)
                payload = json.loads(row.payload)
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

                destination_configs = (
                    db.query(WebhookDestination)
                    .filter(
                        WebhookDestination.url == destination,
                        WebhookDestination.active.is_(True),
                        WebhookDestination.event_type.in_([row.event_type, "*"]),
                    )
                    .order_by(WebhookDestination.id.asc())
                    .all()
                )
                exact_config = next(
                    (item for item in destination_configs if item.event_type == row.event_type),
                    None,
                )
                destination_config = exact_config or (destination_configs[0] if destination_configs else None)
                signing_secret = (
                    destination_config.signing_secret
                    if destination_config and destination_config.signing_secret
                    else settings.outbox_signing_secret
                )
                if not signing_secret:
                    raise RuntimeError("Webhook signing secret is not configured")

                signature = hmac.new(
                    signing_secret.encode("utf-8"),
                    body,
                    hashlib.sha256,
                ).hexdigest()
                response = await client.post(
                    destination,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Flashin-Signature": signature,
                        "X-Flashin-Event-Id": str(row.id),
                        "X-Flashin-Event-Type": row.event_type,
                    },
                )
                if response.status_code < 200 or response.status_code >= 300:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")

                row.destination = destination
                row.status = "sent"
                row.last_error = ""
                row.next_attempt_at = None
                sent += 1
            except Exception as exc:
                schedule_retry(row, f"{exc.__class__.__name__}: {exc}")

    db.commit()
    return sent
