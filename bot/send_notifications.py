import asyncio
import os

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.services.notification_delivery import (
    BATCH_SIZE,
    DELIVERY_ALLOWED,
    DELIVERY_OUTCOME_PERMANENT_FAILURE,
    DELIVERY_OUTCOME_RETRYABLE_FAILURE,
    DELIVERY_OUTCOME_REVIEW_REQUIRED,
    DELIVERY_OUTCOME_SENT,
    DELIVERY_SUPPRESSED,
    finish_delivery,
    claim_pending_batch,
    next_attempt_at,
    preflight_notification_delivery,
    renew_delivery_lease,
    validate_batch_size,
)
from backend.services.pilot_worker_heartbeat import (
    NOTIFICATION_WORKER,
    record_worker_heartbeat,
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
_preflight_notification_delivery_db = preflight_notification_delivery
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


def _preflight_notification_delivery(notification_id: int, lease_token: str) -> str:
    db = SessionLocal()
    try:
        return preflight_notification_delivery(db, notification_id, lease_token)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _finish_delivery(
    notification_id: int,
    lease_token: str,
    error: Exception | None = None,
    *,
    delivery_outcome: str | None = None,
) -> str:
    db = SessionLocal()
    try:
        return finish_delivery(
            db,
            notification_id,
            lease_token,
            error=error,
            delivery_outcome=delivery_outcome,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def classify_telegram_delivery_error(error: Exception) -> str:
    """Classify only outcomes whose provider semantics are safe to automate.

    RetryAfter is an explicit provider rejection before delivery and is safe to
    retry after backoff. Network/server failures are ambiguous because Telegram
    may have accepted the non-idempotent send before the response was lost.
    Other Telegram API errors are explicit terminal rejections. Unknown errors
    during transport are treated conservatively as ambiguous.
    """
    if isinstance(error, TelegramRetryAfter):
        return DELIVERY_OUTCOME_RETRYABLE_FAILURE
    if isinstance(error, (TelegramNetworkError, TelegramServerError)):
        return DELIVERY_OUTCOME_REVIEW_REQUIRED
    if isinstance(error, TelegramAPIError):
        return DELIVERY_OUTCOME_PERMANENT_FAILURE
    return DELIVERY_OUTCOME_REVIEW_REQUIRED


async def send_pending_batch(bot: Bot) -> dict[str, int]:
    claimed = _claim_pending_batch()
    result = {
        "seen": len(claimed),
        "sent": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "review_required": 0,
        "suppressed": 0,
        "ignored": 0,
    }

    for item in claimed:
        notification_id = item["id"]
        lease_token = str(item.get("lease_token") or "")
        if not _renew_delivery_lease(notification_id, lease_token):
            result["ignored"] += 1
            continue

        try:
            chat_id = int(item["telegram_id"])
            if chat_id == 0:
                raise ValueError("Telegram chat id is invalid")
            message = str(item["message"] or "").strip()
            if not message or len(message) > 4096:
                raise ValueError("Telegram notification message is invalid")
        except Exception as exc:
            outcome = _finish_delivery(
                notification_id,
                lease_token,
                error=exc,
                delivery_outcome=DELIVERY_OUTCOME_PERMANENT_FAILURE,
            )
            if outcome in result:
                result[outcome] += 1
            continue

        try:
            if not _renew_delivery_lease(notification_id, lease_token):
                result["ignored"] += 1
                continue

            # This is the authoritative marketing-consent boundary. Every first
            # attempt and every retry passes it after validation/lease renewal
            # and immediately before the external Telegram side effect.
            preflight = _preflight_notification_delivery(notification_id, lease_token)
        except Exception as exc:
            # No Telegram side effect has started, so this path is safe to retry.
            outcome = _finish_delivery(
                notification_id,
                lease_token,
                error=exc,
                delivery_outcome=DELIVERY_OUTCOME_RETRYABLE_FAILURE,
            )
            if outcome in result:
                result[outcome] += 1
            continue

        if preflight == DELIVERY_SUPPRESSED:
            result["suppressed"] += 1
            continue
        if preflight != DELIVERY_ALLOWED:
            result["ignored"] += 1
            continue

        error: Exception | None = None
        delivery_outcome = DELIVERY_OUTCOME_SENT
        try:
            # All DB claim/lease/policy work is committed or rolled back and its
            # short-lived Session is closed before this non-idempotent side effect.
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            error = exc
            delivery_outcome = classify_telegram_delivery_error(exc)

        outcome = _finish_delivery(
            notification_id,
            lease_token,
            error=error,
            delivery_outcome=delivery_outcome,
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
                # Publish liveness only after a complete DB claim/delivery pass.
                # If the notification loop itself is broken, the heartbeat stops
                # and pilot admission fails closed before backlog has to build.
                record_worker_heartbeat(NOTIFICATION_WORKER)
                if result["seen"]:
                    print(result)
            except Exception as exc:
                print({"notification_worker_error": exc.__class__.__name__})
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(worker())
