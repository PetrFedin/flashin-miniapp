import asyncio
import os

from aiogram import Bot
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.services.notification_delivery import (
    BATCH_SIZE,
    finish_delivery,
    claim_pending_batch,
    next_attempt_at,
    renew_delivery_lease,
    validate_batch_size,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://flashin:flashin@db:5432/flashin",
)
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
POLL_SECONDS = max(1.0, float(os.getenv("NOTIFICATION_POLL_SECONDS", "10")))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Compatibility aliases for tests and operational scripts that imported the old
# worker-level helpers. The implementation now lives in the backend service so
# it can be tested without installing the Telegram transport dependency.
_validate_batch_size = validate_batch_size
_next_attempt_at = next_attempt_at
_claim_pending_batch_db = claim_pending_batch
_renew_delivery_lease_db = renew_delivery_lease
_finish_delivery_db = finish_delivery


def _claim_pending_batch() -> list[dict]:
    db = SessionLocal()
    try:
        return claim_pending_batch(db, BATCH_SIZE)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _renew_delivery_lease(notification_id: int, lease_token: str) -> bool:
    db = SessionLocal()
    try:
        return renew_delivery_lease(db, notification_id, lease_token)
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
        return finish_delivery(
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
