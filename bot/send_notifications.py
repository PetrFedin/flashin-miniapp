import asyncio
import os
from datetime import datetime

from aiogram import Bot
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# This script can be run by cron/worker. It sends pending notification rows.
from backend.models import Notification

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://flashin:flashin@db:5432/flashin")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    bot = Bot(BOT_TOKEN)
    db = SessionLocal()
    try:
        rows = db.query(Notification).filter(Notification.status == "pending").limit(50).all()
        for row in rows:
            try:
                await bot.send_message(chat_id=row.telegram_id, text=row.message)
                row.status = "sent"
                row.sent_at = datetime.utcnow()
            except Exception as exc:
                row.status = "failed"
                row.error = str(exc)
        db.commit()
    finally:
        db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
