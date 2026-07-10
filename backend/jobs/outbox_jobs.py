import json
import hashlib
import hmac
from datetime import datetime
import httpx
from ..config import get_settings
from sqlalchemy.orm import Session
from ..models import WebhookOutbox
from ..services.outbox import schedule_retry


async def process_outbox(db: Session) -> int:
    rows = (
        db.query(WebhookOutbox)
        .filter(WebhookOutbox.status == "pending")
        .filter((WebhookOutbox.next_attempt_at == None) | (WebhookOutbox.next_attempt_at <= datetime.utcnow()))
        .limit(50)
        .all()
    )
    sent = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for row in rows:
            try:
                payload = json.loads(row.payload)
                body = json.dumps(payload, ensure_ascii=False).encode()
                signature = hmac.new(get_settings().outbox_signing_secret.encode(), body, hashlib.sha256).hexdigest()
                response = await client.post(row.destination, content=body, headers={"Content-Type": "application/json", "X-Flashin-Signature": signature})
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
                row.status = "sent"
                row.last_error = ""
                sent += 1
            except Exception as exc:
                schedule_retry(row, str(exc))
    db.commit()
    return sent
