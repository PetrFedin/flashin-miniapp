from sqlalchemy import text
from sqlalchemy.orm import Session
from ..config import get_settings


def run_diagnostics(db: Session) -> dict:
    settings = get_settings()
    checks = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}

    required_env = [
        "telegram_bot_token",
        "jwt_secret",
        "admin_email",
        "admin_password",
        "mini_app_url",
        "api_public_url",
    ]
    env_missing = []
    for key in required_env:
        value = getattr(settings, key, "")
        if not value or value in {"change-me-now", "replace_with_botfather_token", "replace_with_long_random_secret"}:
            env_missing.append(key)
    checks["env"] = {"ok": not env_missing, "missing_or_default": env_missing}

    checks["payments"] = {
        "ok": bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        "provider": settings.payment_provider,
    }

    checks["moysklad"] = {
        "ok": bool(settings.moysklad_token or (settings.moysklad_login and settings.moysklad_password)),
    }

    checks["media"] = {
        "ok": bool(settings.media_storage),
        "storage": settings.media_storage,
    }

    checks["search"] = {
        "ok": True,
        "meilisearch_enabled": settings.meilisearch_enabled,
    }

    overall = all(v.get("ok") for v in checks.values())
    return {"ok": overall, "checks": checks}
