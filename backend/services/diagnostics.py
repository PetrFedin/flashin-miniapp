from datetime import timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import MoySkladSyncLog, Notification, WebhookOutbox
from ..notification_models import NotificationDeliveryState


def _error_name(exc: Exception) -> str:
    return exc.__class__.__name__


def run_diagnostics(db: Session) -> dict:
    settings = get_settings()
    checks: dict[str, dict] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": _error_name(exc)}

    required_env = [
        "telegram_bot_token",
        "jwt_secret",
        "admin_email",
        "admin_password",
        "mini_app_url",
        "api_public_url",
    ]
    env_missing = []
    weak_defaults = {
        "change-me-now",
        "change-me",
        "replace_with_botfather_token",
        "replace_with_long_random_secret",
    }
    for key in required_env:
        value = getattr(settings, key, "")
        if not value or value in weak_defaults:
            env_missing.append(key)
    checks["env"] = {"ok": not env_missing, "missing_or_default": env_missing}

    checks["payments"] = {
        "ok": bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        "provider": settings.payment_provider,
    }

    moysklad_configured = bool(
        settings.moysklad_token
        or (settings.moysklad_login and settings.moysklad_password)
    )
    checks["moysklad"] = {
        "ok": moysklad_configured and 5 <= settings.moysklad_sync_interval_minutes <= 1440,
        "configured": moysklad_configured,
        "sync_interval_minutes": settings.moysklad_sync_interval_minutes,
    }

    checks["scheduler"] = {
        "ok": settings.scheduler_enabled or settings.app_env != "production",
        "enabled": settings.scheduler_enabled,
    }

    media_ok = bool(settings.media_storage)
    if settings.media_storage in {"s3", "r2"}:
        media_ok = all(
            [
                settings.media_public_base_url,
                settings.s3_endpoint_url,
                settings.s3_bucket,
                settings.s3_access_key_id,
                settings.s3_secret_access_key,
            ]
        )
    checks["media"] = {
        "ok": media_ok,
        "storage": settings.media_storage,
    }

    if settings.meilisearch_enabled:
        try:
            response = httpx.get(
                f"{settings.meilisearch_url.rstrip('/')}/health",
                timeout=2.0,
            )
            search_ok = response.status_code == 200
            checks["search"] = {
                "ok": search_ok,
                "enabled": True,
                "status_code": response.status_code,
            }
        except Exception as exc:
            checks["search"] = {
                "ok": False,
                "enabled": True,
                "error": _error_name(exc),
            }
    else:
        checks["search"] = {
            "ok": settings.app_env != "production",
            "enabled": False,
        }

    now = utcnow_naive()
    try:
        failed_notifications = (
            db.query(Notification)
            .filter(Notification.status == "failed")
            .count()
        )
        due_notification_retries = (
            db.query(NotificationDeliveryState)
            .join(Notification, Notification.id == NotificationDeliveryState.notification_id)
            .filter(
                Notification.status == "pending",
                NotificationDeliveryState.next_attempt_at.is_not(None),
                NotificationDeliveryState.next_attempt_at <= now,
            )
            .count()
        )
        checks["notification_delivery"] = {
            "ok": failed_notifications == 0,
            "failed": failed_notifications,
            "due_retries": due_notification_retries,
        }
    except Exception as exc:
        checks["notification_delivery"] = {
            "ok": False,
            "error": _error_name(exc),
        }

    try:
        failed_outbox = (
            db.query(WebhookOutbox)
            .filter(WebhookOutbox.status == "failed")
            .count()
        )
        due_outbox = (
            db.query(WebhookOutbox)
            .filter(
                WebhookOutbox.status == "pending",
                (WebhookOutbox.next_attempt_at.is_(None))
                | (WebhookOutbox.next_attempt_at <= now),
            )
            .count()
        )
        checks["webhook_outbox"] = {
            "ok": failed_outbox == 0,
            "failed": failed_outbox,
            "due": due_outbox,
        }
    except Exception as exc:
        checks["webhook_outbox"] = {
            "ok": False,
            "error": _error_name(exc),
        }

    try:
        stuck_before = now - timedelta(hours=1)
        stuck_syncs = (
            db.query(MoySkladSyncLog)
            .filter(
                MoySkladSyncLog.status == "started",
                MoySkladSyncLog.created_at < stuck_before,
            )
            .count()
        )
        latest_sync = (
            db.query(MoySkladSyncLog)
            .order_by(MoySkladSyncLog.created_at.desc(), MoySkladSyncLog.id.desc())
            .first()
        )
        latest_status = latest_sync.status if latest_sync else "never"
        latest_ok = latest_status != "failed"
        checks["moysklad_sync"] = {
            "ok": stuck_syncs == 0 and latest_ok,
            "stuck": stuck_syncs,
            "latest_status": latest_status,
            "latest_finished_at": latest_sync.finished_at if latest_sync else None,
        }
    except Exception as exc:
        checks["moysklad_sync"] = {
            "ok": False,
            "error": _error_name(exc),
        }

    overall = all(value.get("ok") for value in checks.values())
    return {
        "ok": overall,
        "checked_at": now,
        "checks": checks,
    }
