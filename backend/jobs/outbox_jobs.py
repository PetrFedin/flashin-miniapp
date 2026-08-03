import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import WebhookDestination
from ..services.webhook_security import (
    is_internal_destination,
    resolve_public_webhook_addresses,
)

_OUTBOX_BATCH_SIZE = 50
_OUTBOX_LEASE_MINUTES = 5
_MAX_OUTBOX_ATTEMPTS = 10
_MAX_WEBHOOK_BODY_BYTES = 256 * 1024


def _validate_batch_size(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Outbox batch size must be an integer")
    if limit < 1 or limit > 500:
        raise ValueError("Outbox batch size must be between 1 and 500")
    return limit


def _claim_outbox(db: Session, limit: int = _OUTBOX_BATCH_SIZE) -> list[dict]:
    batch_size = _validate_batch_size(limit)
    now = utcnow_naive()
    lease_until = now + timedelta(minutes=_OUTBOX_LEASE_MINUTES)
    lease_token = uuid.uuid4().hex
    rows = db.execute(
        text(
            """
            WITH candidates AS (
                SELECT id
                FROM webhook_outbox
                WHERE
                    (
                        status = 'pending'
                        AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
                    )
                    OR
                    (
                        status = 'processing'
                        AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
                    )
                ORDER BY id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE webhook_outbox AS target
            SET
                status = 'processing',
                next_attempt_at = :lease_until,
                lease_token = :lease_token
            FROM candidates
            WHERE target.id = candidates.id
            RETURNING
                target.id,
                target.destination,
                target.event_type,
                target.payload,
                target.lease_token
            """
        ),
        {
            "now": now,
            "limit": batch_size,
            "lease_until": lease_until,
            "lease_token": lease_token,
        },
    ).mappings().all()
    db.commit()
    return sorted((dict(row) for row in rows), key=lambda row: row["id"])


def _renew_outbox_lease(db: Session, row_id: int, lease_token: str) -> bool:
    normalized_token = str(lease_token or "").strip()
    if not normalized_token:
        return False
    lease_until = utcnow_naive() + timedelta(minutes=_OUTBOX_LEASE_MINUTES)
    row = db.execute(
        text(
            """
            UPDATE webhook_outbox
            SET next_attempt_at = :lease_until
            WHERE
                id = :row_id
                AND status = 'processing'
                AND lease_token = :lease_token
            RETURNING id
            """
        ),
        {
            "row_id": row_id,
            "lease_token": normalized_token,
            "lease_until": lease_until,
        },
    ).first()
    db.commit()
    return row is not None


def _finish_outbox(
    db: Session,
    row_id: int,
    lease_token: str,
    *,
    success: bool,
    destination: str = "",
    error: str = "",
) -> bool:
    normalized_token = str(lease_token or "").strip()
    if not normalized_token:
        return False

    if success:
        row = db.execute(
            text(
                """
                UPDATE webhook_outbox
                SET
                    destination = CASE
                        WHEN :destination = '' THEN destination
                        ELSE :destination
                    END,
                    status = 'sent',
                    last_error = '',
                    next_attempt_at = NULL,
                    lease_token = NULL
                WHERE
                    id = :row_id
                    AND status = 'processing'
                    AND lease_token = :lease_token
                RETURNING id
                """
            ),
            {
                "row_id": row_id,
                "lease_token": normalized_token,
                "destination": destination,
            },
        ).first()
        db.commit()
        return row is not None

    current = db.execute(
        text(
            """
            SELECT attempts
            FROM webhook_outbox
            WHERE
                id = :row_id
                AND status = 'processing'
                AND lease_token = :lease_token
            FOR UPDATE
            """
        ),
        {"row_id": row_id, "lease_token": normalized_token},
    ).first()
    if current is None:
        db.rollback()
        return False

    attempts = max(int(current.attempts or 0), 0) + 1
    failed = attempts >= _MAX_OUTBOX_ATTEMPTS
    next_attempt_at = None
    if not failed:
        next_attempt_at = utcnow_naive() + timedelta(
            minutes=min(60, 2**attempts)
        )
    updated = db.execute(
        text(
            """
            UPDATE webhook_outbox
            SET
                attempts = :attempts,
                last_error = :error,
                status = :status,
                next_attempt_at = :next_attempt_at,
                lease_token = NULL
            WHERE
                id = :row_id
                AND status = 'processing'
                AND lease_token = :lease_token
            RETURNING id
            """
        ),
        {
            "row_id": row_id,
            "lease_token": normalized_token,
            "attempts": attempts,
            "error": (error or "Webhook delivery failed")[:2000],
            "status": "failed" if failed else "pending",
            "next_attempt_at": next_attempt_at,
        },
    ).first()
    db.commit()
    return updated is not None


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
            lease_token = item["lease_token"]
            if not _renew_outbox_lease(db, row_id, lease_token):
                continue

            raw_destination = item["destination"]
            if is_internal_destination(raw_destination):
                if _finish_outbox(
                    db,
                    row_id,
                    lease_token,
                    success=True,
                ):
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
                    allow_nan=False,
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

                if not _renew_outbox_lease(db, row_id, lease_token):
                    continue
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
                    lease_token,
                    success=True,
                    destination=normalized_destination,
                ):
                    sent += 1
            except Exception as exc:
                _finish_outbox(
                    db,
                    row_id,
                    lease_token,
                    success=False,
                    error=f"{exc.__class__.__name__}: {exc}"[:2000],
                )

    return sent
