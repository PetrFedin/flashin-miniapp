import asyncio
import os
import uuid
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import and_, create_engine, or_
from sqlalchemy.orm import Session, sessionmaker

from backend.database import utcnow_naive
from backend.models import Notification
from backend.notification_models import NotificationDeliveryState

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://flashin:flashin@db:5432/flashin",
)
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
BATCH_SIZE = max(1, min(int(os.getenv("NOTIFICATION_BATCH_SIZE", "50")), 200))
POLL_SECONDS = max(1.0, float(os.getenv("NOTIFICATION_POLL_SECONDS", "10")))
MAX_ATTEMPTS = max(1, min(int(os.getenv("NOTIFICATION_MAX_ATTEMPTS", "5")), 20))
INITIAL_BACKOFF_SECONDS = max(5, int(os.getenv("NOTIFICATION_INITIAL_BACKOFF_SECONDS", "30")))
MAX_BACKOFF_SECONDS = max(
    INITIAL_BACKOFF_SECONDS,
    int(os.getenv("NOTIFICATION_MAX_BACKOFF_SECONDS", "3600")),
)
LEASE_SECONDS = max(30, int(os.getenv("NOTIFICATION_LEASE_SECONDS", "180")))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _validate_batch_size(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Notification batch size must be an integer")
    if limit < 1 or limit > 200:
        raise ValueError("Notification batch size must be between 1 and 200")
    return limit


def _next_attempt_at(attempts: int, *, now: datetime | None = None) -> datetime:
    delay = min(
        INITIAL_BACKOFF_SECONDS * (2 ** max(attempts - 1, 0)),
        MAX_BACKOFF_SECONDS,
    )
    return (now or utcnow_naive()) + timedelta(seconds=delay)


def _claim_pending_batch_db(
    db: Session,
    limit: int = BATCH_SIZE,
) -> list[dict]:
    batch_size = _validate_batch_size(limit)
    now = utcnow_naive()
    rows = (
        db.query(Notification)
        .outerjoin(
            NotificationDeliveryState,
            NotificationDeliveryState.notification_id == Notification.id,
        )
        .filter(
            or_(
                and_(
                    Notification.status == "pending",
                    or_(
                        NotificationDeliveryState.id.is_(None),
                        NotificationDeliveryState.next_attempt_at.is_(None),
                        NotificationDeliveryState.next_attempt_at <= now,
                    ),
                ),
                and_(
                    Notification.status == "processing",
                    or_(
                        NotificationDeliveryState.next_attempt_at.is_(None),
                        NotificationDeliveryState.next_attempt_at <= now,
                    ),
                ),
            )
        )
        .filter(
            or_(
                NotificationDeliveryState.id.is_(None),
                NotificationDeliveryState.attempts < MAX_ATTEMPTS,
            )
        )
        .order_by(Notification.created_at.asc(), Notification.id.asc())
        .with_for_update(of=Notification, skip_locked=True)
        .limit(batch_size)
        .all()
    )

    claimed: list[dict] = []
    lease_until = now + timedelta(seconds=LEASE_SECONDS)
    for row in rows:
        state = (
            db.query(NotificationDeliveryState)
            .filter(NotificationDeliveryState.notification_id == row.id)
            .with_for_update()
            .first()
        )
        if not state:
            state = NotificationDeliveryState(
                notification_id=row.id,
                attempts=0,
            )
            db.add(state)
            db.flush()

        lease_token = uuid.uuid4().hex
        row.status = "processing"
        state.next_attempt_at = lease_until
        state.lease_token = lease_token
        state.updated_at = now
        claimed.append(
            {
                "id": row.id,
                "telegram_id": row.telegram_id,
                "message": row.message,
                "lease_until": lease_until,
                "lease_token": lease_token,
            }
        )

    db.commit()
    return claimed


def _claim_pending_batch() -> list[dict]:
    db = SessionLocal()
    try:
        return _claim_pending_batch_db(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _renew_delivery_lease_db(
    db: Session,
    notification_id: int,
    lease_token: str,
) -> bool:
    normalized_token = str(lease_token or "").strip()
    if not normalized_token:
        return False

    row = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.status == "processing",
        )
        .with_for_update()
        .first()
    )
    if not row:
        db.rollback()
        return False

    state = (
        db.query(NotificationDeliveryState)
        .filter(
            NotificationDeliveryState.notification_id == row.id,
            NotificationDeliveryState.lease_token == normalized_token,
        )
        .with_for_update()
        .first()
    )
    if not state:
        db.rollback()
        return False

    now = utcnow_naive()
    state.next_attempt_at = now + timedelta(seconds=LEASE_SECONDS)
    state.updated_at = now
    db.commit()
    return True


def _renew_delivery_lease(notification_id: int, lease_token: str) -> bool:
    db = SessionLocal()
    try:
        return _renew_delivery_lease_db(db, notification_id, lease_token)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _finish_delivery_db(
    db: Session,
    notification_id: int,
    lease_token: str,
    error: Exception | None = None,
) -> str:
    normalized_token = str(lease_token or "").strip()
    if not normalized_token:
        return "ignored"

    row = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.status == "processing",
        )
        .with_for_update()
        .first()
    )
    if not row:
        db.rollback()
        return "ignored"

    state = (
        db.query(NotificationDeliveryState)
        .filter(
            NotificationDeliveryState.notification_id == row.id,
            NotificationDeliveryState.lease_token == normalized_token,
        )
        .with_for_update()
        .first()
    )
    if not state:
        db.rollback()
        return "ignored"

    now = utcnow_naive()
    if error is None:
        row.status = "sent"
        row.sent_at = now
        row.error = ""
        db.delete(state)
        db.commit()
        return "sent"

    state.attempts = max(int(state.attempts or 0), 0) + 1
    state.updated_at = now
    state.last_error = f"{error.__class__.__name__}: {error}"[:2000]
    state.lease_token = None
    row.error = state.last_error
    if state.attempts >= MAX_ATTEMPTS:
        row.status = "failed"
        state.next_attempt_at = None
        outcome = "failed"
    else:
        row.status = "pending"
        state.next_attempt_at = _next_attempt_at(state.attempts, now=now)
        outcome = "retry_scheduled"
    db.commit()
    return outcome


def _finish_delivery(
    notification_id: int,
    lease_token: str,
    error: Exception | None = None,
) -> str:
    db = SessionLocal()
    try:
        return _finish_delivery_db(
            db,
            notification_id,
            lease_token,
            error=error,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def send_pending_batch(bot: Bot) -> dict[str, int]:
    claimed = _claim_pending_batch()
    result = {
        "seen": len(claimed),
        "sent": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "ignored": 0,
    }

    for item in claimed:
        notification_id = item["id"]
        lease_token = str(item.get("lease_token") or "")
        if not _renew_delivery_lease(notification_id, lease_token):
            result["ignored"] += 1
            continue

        error: Exception | None = None
        try:
            chat_id = int(item["telegram_id"])
            if chat_id == 0:
                raise ValueError("Telegram chat id is invalid")
            message = str(item["message"] or "").strip()
            if not message or len(message) > 4096:
                raise ValueError("Telegram notification message is invalid")
            if not _renew_delivery_lease(notification_id, lease_token):
                result["ignored"] += 1
                continue
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            error = exc

        outcome = _finish_delivery(
            notification_id,
            lease_token,
            error=error,
        )
        if outcome in result:
            result[outcome] += 1

    return result


async def worker() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    bot = Bot(BOT_TOKEN)
    try:
        while True:
            try:
                result = await send_pending_batch(bot)
                if result["seen"]:
                    print(result)
            except Exception as exc:
                print({"notification_worker_error": exc.__class__.__name__})
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(worker())
