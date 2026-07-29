#!/usr/bin/env python3
import sys
from pathlib import Path
from urllib.parse import urlparse

ENV_PATH = Path(".env")
PLACEHOLDER_VALUES = {
    "change-me",
    "change-this-before-launch",
    "replace_with_botfather_token",
    "replace_with_long_random_secret",
    "STRONG_PASSWORD",
}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def require_https(env: dict[str, str], key: str, invalid: list[str]) -> None:
    value = env.get(key, "")
    if value and urlparse(value).scheme != "https":
        invalid.append(f"{key} must use https in production")


def validate_int(
    env: dict[str, str],
    key: str,
    minimum: int,
    maximum: int,
    invalid: list[str],
) -> int | None:
    value = env.get(key)
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except ValueError:
        invalid.append(f"{key} must be an integer")
        return None
    if parsed < minimum or parsed > maximum:
        invalid.append(f"{key} must be between {minimum} and {maximum}")
    return parsed


def validate_float(
    env: dict[str, str],
    key: str,
    minimum: float,
    maximum: float,
    invalid: list[str],
) -> float | None:
    value = env.get(key)
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        invalid.append(f"{key} must be numeric")
        return None
    if parsed < minimum or parsed > maximum:
        invalid.append(f"{key} must be between {minimum} and {maximum}")
    return parsed


if not ENV_PATH.exists():
    print(".env not found")
    sys.exit(1)

env = load_env(ENV_PATH)
app_env = env.get("APP_ENV", "development").strip().lower()
is_production = app_env == "production"

required = [
    "DATABASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "JWT_SECRET",
    "ADMIN_EMAIL",
    "ADMIN_PASSWORD",
    "MINI_APP_URL",
    "API_PUBLIC_URL",
]
if is_production:
    required.extend(
        [
            "CORS_ORIGINS",
            "YOOKASSA_SHOP_ID",
            "YOOKASSA_SECRET_KEY",
            "OUTBOX_SIGNING_SECRET",
            "NOTIFICATION_BATCH_SIZE",
            "NOTIFICATION_POLL_SECONDS",
            "NOTIFICATION_MAX_ATTEMPTS",
            "NOTIFICATION_INITIAL_BACKOFF_SECONDS",
            "NOTIFICATION_MAX_BACKOFF_SECONDS",
        ]
    )

media_storage = env.get("MEDIA_STORAGE", "local").strip().lower()
if is_production and media_storage in {"s3", "r2"}:
    required.extend(
        [
            "MEDIA_PUBLIC_BASE_URL",
            "S3_ENDPOINT_URL",
            "S3_BUCKET",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
        ]
    )
if is_production and is_true(env.get("MEILISEARCH_ENABLED")):
    required.append("MEILISEARCH_MASTER_KEY")

missing = sorted({key for key in required if not env.get(key)})
weak: list[str] = []
invalid: list[str] = []

for key, value in env.items():
    normalized = value.strip()
    if normalized in PLACEHOLDER_VALUES or any(marker in normalized for marker in PLACEHOLDER_VALUES):
        weak.append(key)

jwt_secret = env.get("JWT_SECRET", "")
if jwt_secret and len(jwt_secret) < 32:
    invalid.append("JWT_SECRET must contain at least 32 characters")
admin_password = env.get("ADMIN_PASSWORD", "")
if admin_password and len(admin_password) < 12:
    invalid.append("ADMIN_PASSWORD must contain at least 12 characters")
if is_production:
    outbox_secret = env.get("OUTBOX_SIGNING_SECRET", "")
    if outbox_secret and len(outbox_secret) < 32:
        invalid.append("OUTBOX_SIGNING_SECRET must contain at least 32 characters")

    database_url = env.get("DATABASE_URL", "")
    if "flashin:flashin@" in database_url:
        invalid.append("DATABASE_URL uses development credentials")
    if "localhost" in env.get("CORS_ORIGINS", "") or "127.0.0.1" in env.get("CORS_ORIGINS", ""):
        invalid.append("CORS_ORIGINS contains a local address in production")

    for url_key in ("MINI_APP_URL", "API_PUBLIC_URL", "ADMIN_URL", "YOOKASSA_RETURN_URL"):
        require_https(env, url_key, invalid)
    if media_storage in {"s3", "r2"}:
        require_https(env, "MEDIA_PUBLIC_BASE_URL", invalid)

    if not env.get("MOYSKLAD_TOKEN") and not (
        env.get("MOYSKLAD_LOGIN") and env.get("MOYSKLAD_PASSWORD")
    ):
        invalid.append("Configure MOYSKLAD_TOKEN or MOYSKLAD_LOGIN and MOYSKLAD_PASSWORD")

batch_size = validate_int(env, "NOTIFICATION_BATCH_SIZE", 1, 200, invalid)
poll_seconds = validate_float(env, "NOTIFICATION_POLL_SECONDS", 1.0, 3600.0, invalid)
max_attempts = validate_int(env, "NOTIFICATION_MAX_ATTEMPTS", 1, 20, invalid)
initial_backoff = validate_int(env, "NOTIFICATION_INITIAL_BACKOFF_SECONDS", 5, 86400, invalid)
max_backoff = validate_int(env, "NOTIFICATION_MAX_BACKOFF_SECONDS", 5, 604800, invalid)
if initial_backoff is not None and max_backoff is not None and max_backoff < initial_backoff:
    invalid.append("NOTIFICATION_MAX_BACKOFF_SECONDS must be >= NOTIFICATION_INITIAL_BACKOFF_SECONDS")

if missing or weak or invalid:
    print(
        {
            "missing": missing,
            "weak_defaults": sorted(set(weak)),
            "invalid": sorted(set(invalid)),
        }
    )
    sys.exit(1)

print(
    {
        "status": "Environment OK",
        "app_env": app_env,
        "notification_batch_size": batch_size,
        "notification_poll_seconds": poll_seconds,
        "notification_max_attempts": max_attempts,
    }
)
