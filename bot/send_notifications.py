import asyncio
import os
from datetime import datetime

from aiogram import Bot
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Notification

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://flashin:flashin@db:5432/flashin",
)
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
BATCH_SIZE = max(1, min(int(os.getenv("NOTIFICATION_BATCH_SIZE", "50")), 200))
POLL_SECONDS = max(1.0, float(os.getenv("NOTIFICATION_POLL_SECONDS", "10")))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


async def send_pending_batch(bot: Bot) -> int:
    db = SessionLocal()
    sent = 0
    try:
        rows = (
            db.query(Notification)
            .filter(Notification.status == "pending")
            .order_by(Notification.created_at.asc(), Notification.id.asc())
            .with_for_update(skip_locked=True)
            .limit(BATCH_SIZE)
            .all()
        )
        for row in rows:
            try:
                await bot.send_message(chat_id=row.telegram_id, text=row.message)
                row.status = "sent"
                row.sent_at = datetime.utcnow()
                row.error = ""
                sent += 1
            except Exception as exc:
                row.status = "failed"
                row.error = f"{exc.__class__.__name__}: {exc}"[:2000]
        db.commit()
        return sent
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
                sent = await send_pending_batch(bot)
                if sent:
                    print({"notifications_sent": sent})
            except Exception as exc:
                print({"notification_worker_error": exc.__class__.__name__})
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(worker())
