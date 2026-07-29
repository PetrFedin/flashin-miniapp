import asyncio
import os
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker

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

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _next_attempt_at(attempts: int) -> datetime:
    delay = min(INITIAL_BACKOFF_SECONDS * (2 ** max(attempts - 1, 0)), MAX_BACKOFF_SECONDS)
    return datetime.utcnow() + timedelta(seconds=delay)


async def send_pending_batch(bot: Bot) -> dict[str, int]:
    db = SessionLocal()
    result = {"seen": 0, "sent": 0, "retry_scheduled": 0, "failed": 0}
    try:
        now = datetime.utcnow()
        rows = (
            db.query(Notification)
            .outerjoin(
                NotificationDeliveryState,
                NotificationDeliveryState.notification_id == Notification.id,
            )
            .filter(Notification.status == "pending")
            .filter(
                or_(
                    NotificationDeliveryState.id.is_(None),
                    NotificationDeliveryState.next_attempt_at.is_(None),
                    NotificationDeliveryState.next_attempt_at <= now,
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
            .limit(BATCH_SIZE)
            .all()
        )
        result["seen"] = len(rows)

        for row in rows:
            state = (
                db.query(NotificationDeliveryState)
                .filter(NotificationDeliveryState.notification_id == row.id)
                .with_for_update()
                .first()
            )
            if not state:
                state = NotificationDeliveryState(notification_id=row.id, attempts=0)
                db.add(state)
                db.flush()

            try:
                await bot.send_message(chat_id=row.telegram_id, text=row.message)
                row.status = "sent"
                row.sent_at = datetime.utcnow()
                row.error = ""
                db.delete(state)
                result["sent"] += 1
            except Exception as exc:
                state.attempts += 1
                state.updated_at = datetime.utcnow()
                state.last_error = f"{exc.__class__.__name__}: {exc}"[:2000]
                row.error = state.last_error
                if state.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                    state.next_attempt_at = None
                    result["failed"] += 1
                else:
                    row.status = "pending"
                    state.next_attempt_at = _next_attempt_at(state.attempts)
                    result["retry_scheduled"] += 1

        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
