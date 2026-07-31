import asyncio
import os
from datetime import datetime

from aiogram import Bot
from sqlalchemy import and_, create_engine, or_
from sqlalchemy.orm import sessionmaker

from backend.models import Notification
from backend.notification_models import NotificationDeliveryState
from backend.notification_statuses import (
    MAX_NOTIFICATION_ATTEMPTS,
    NOTIFICATION_PENDING,
    NOTIFICATION_PROCESSING,
)
from backend.services.notification_delivery import (
    claim_notification_delivery,
    complete_notification_delivery,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://flashin:flashin@db:5432/flashin",
)
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
BATCH_SIZE = max(1, min(int(os.getenv("NOTIFICATION_BATCH_SIZE", "50")), 200))
POLL_SECONDS = max(1.0, float(os.getenv("NOTIFICATION_POLL_SECONDS", "10")))
MAX_ATTEMPTS = max(
    1,
    min(
        int(os.getenv("NOTIFICATION_MAX_ATTEMPTS", "5")),
        MAX_NOTIFICATION_ATTEMPTS,
    ),
)
INITIAL_BACKOFF_SECONDS = max(5, int(os.getenv("NOTIFICATION_INITIAL_BACKOFF_SECONDS", "30")))
MAX_BACKOFF_SECONDS = max(
    INITIAL_BACKOFF_SECONDS,
    int(os.getenv("NOTIFICATION_MAX_BACKOFF_SECONDS", "3600")),
)
LEASE_SECONDS = max(30, int(os.getenv("NOTIFICATION_LEASE_SECONDS", "180")))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class DeliveryOutcomeUnknown(RuntimeError):
    pass


def _claim_pending_batch() -> list[dict]:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        rows = (
            db.query(Notification)
            .outerjoin(
                NotificationDeliveryState,
                NotificationDeliveryState.notification_id == Notification.id,
            )
            .filter(
                or_(
                    and_(
                        Notification.status == NOTIFICATION_PENDING,
                        or_(
                            NotificationDeliveryState.id.is_(None),
                            NotificationDeliveryState.next_attempt_at.is_(None),
                            NotificationDeliveryState.next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        Notification.status == NOTIFICATION_PROCESSING,
                        or_(
                            NotificationDeliveryState.id.is_(None),
                            NotificationDeliveryState.next_attempt_at.is_(None),
                            NotificationDeliveryState.next_attempt_at <= now,
                        ),
                    ),
                )
            )
            .order_by(Notification.created_at.asc(), Notification.id.asc())
            .with_for_update(of=Notification, skip_locked=True)
            .limit(BATCH_SIZE)
            .all()
        )

        claimed: list[dict] = []
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
                    next_attempt_at=now,
                    last_error="",
                    deduplication_key="",
                    lease_token="",
                )
                db.add(state)
                db.flush()

            lease_token = claim_notification_delivery(
                row,
                state,
                now=now,
                lease_seconds=LEASE_SECONDS,
                max_attempts=MAX_ATTEMPTS,
            )
            if not lease_token:
                continue
            claimed.append(
                {
                    "id": row.id,
                    "telegram_id": row.telegram_id,
                    "message": row.message,
                    "lease_token": lease_token,
                }
            )

        db.commit()
        return claimed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _finish_delivery(
    notification_id: int,
    lease_token: str,
    error: Exception | None = None,
) -> str:
    db = SessionLocal()
    try:
        row = (
            db.query(Notification)
            .filter(Notification.id == notification_id)
            .with_for_update()
            .first()
        )
        if not row:
            db.rollback()
            return "ignored"

        state = (
            db.query(NotificationDeliveryState)
            .filter(NotificationDeliveryState.notification_id == row.id)
            .with_for_update()
            .first()
        )
        if not state:
            db.rollback()
            return "ignored"

        outcome = complete_notification_delivery(
            row,
            state,
            lease_token,
            error,
            max_attempts=MAX_ATTEMPTS,
            initial_backoff_seconds=INITIAL_BACKOFF_SECONDS,
            max_backoff_seconds=MAX_BACKOFF_SECONDS,
        )
        if outcome == "ignored":
            db.rollback()
            return outcome
        db.commit()
        return outcome
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
        error: Exception | None = None
        try:
            chat_id = int(item["telegram_id"])
            if chat_id == 0:
                raise ValueError("Telegram chat id is invalid")
            message = str(item["message"] or "").strip()
            if not message or len(message) > 4096:
                raise ValueError("Telegram notification message is invalid")
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                disable_web_page_preview=True,
            )
        except asyncio.CancelledError:
            _finish_delivery(
                item["id"],
                item["lease_token"],
                error=DeliveryOutcomeUnknown(
                    "Worker was cancelled while Telegram delivery outcome was unknown"
                ),
            )
            raise
        except Exception as exc:
            error = exc

        outcome = _finish_delivery(
            item["id"],
            item["lease_token"],
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
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print({"notification_worker_error": exc.__class__.__name__})
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(worker())
