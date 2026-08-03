#!/usr/bin/env python3
import hmac
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

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


def validate_database_credentials(env: dict[str, str], invalid: list[str]) -> None:
    database_url = env.get("DATABASE_URL", "")
    if not database_url:
        return
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("postgresql"):
        invalid.append("DATABASE_URL must use PostgreSQL")
        return

    url_user = unquote(parsed.username or "")
    url_password = unquote(parsed.password or "")
    url_database = unquote(parsed.path.lstrip("/"))
    url_host = parsed.hostname or ""

    if url_host != "db":
        invalid.append("DATABASE_URL host must be db for the bundled production Compose deployment")
    if url_user != env.get("POSTGRES_USER", ""):
        invalid.append("DATABASE_URL user does not match POSTGRES_USER")
    if url_password != env.get("POSTGRES_PASSWORD", ""):
        invalid.append("DATABASE_URL password does not match POSTGRES_PASSWORD")
    if url_database != env.get("POSTGRES_DB", ""):
        invalid.append("DATABASE_URL database does not match POSTGRES_DB")


def distinct_secret(
    env: dict[str, str],
    left: str,
    right: str,
    invalid: list[str],
) -> None:
    left_value = env.get(left, "")
    right_value = env.get(right, "")
    if left_value and right_value and hmac.compare_digest(
        left_value.encode("utf-8"), right_value.encode("utf-8")
    ):
        invalid.append(f"{left} must differ from {right}")


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
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "CORS_ORIGINS",
            "ADMIN_URL",
            "YOOKASSA_SHOP_ID",
            "YOOKASSA_SECRET_KEY",
            "OUTBOX_SIGNING_SECRET",
            "ADMIN_TOTP_ENCRYPTION_KEY",
            "PILOT_EVIDENCE_SIGNING_SECRET",
            "PILOT_PROVIDER_EVIDENCE_MAX_AGE_MINUTES",
            "PILOT_LIVE_GATE_MAX_AGE_MINUTES",
            "PILOT_ADMISSION_MAX_AGE_MINUTES",
            "PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS",
            "PILOT_RUNTIME_ENFORCED",
            "PILOT_RUNTIME_MAX_ORDERS",
            "MEDIA_STORAGE",
            "MEILISEARCH_ENABLED",
            "SCHEDULER_ENABLED",
            "MOYSKLAD_SYNC_INTERVAL_MINUTES",
            "MOYSKLAD_SALE_PRICE_TYPE",
            "MOYSKLAD_SIZE_ATTRIBUTE_NAMES",
            "MOYSKLAD_COLOR_ATTRIBUTE_NAMES",
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
    totp_encryption_key = env.get("ADMIN_TOTP_ENCRYPTION_KEY", "")
    if totp_encryption_key and len(totp_encryption_key) < 32:
        invalid.append("ADMIN_TOTP_ENCRYPTION_KEY must contain at least 32 characters")

    outbox_secret = env.get("OUTBOX_SIGNING_SECRET", "")
    if outbox_secret and len(outbox_secret) < 32:
        invalid.append("OUTBOX_SIGNING_SECRET must contain at least 32 characters")

    pilot_secret = env.get("PILOT_EVIDENCE_SIGNING_SECRET", "")
    if pilot_secret and len(pilot_secret) < 32:
        invalid.append("PILOT_EVIDENCE_SIGNING_SECRET must contain at least 32 characters")
    for other in ("JWT_SECRET", "ADMIN_TOTP_ENCRYPTION_KEY", "OUTBOX_SIGNING_SECRET"):
        distinct_secret(env, "PILOT_EVIDENCE_SIGNING_SECRET", other, invalid)
    distinct_secret(env, "ADMIN_TOTP_ENCRYPTION_KEY", "JWT_SECRET", invalid)
    distinct_secret(env, "OUTBOX_SIGNING_SECRET", "JWT_SECRET", invalid)

    validate_database_credentials(env, invalid)
    if "flashin:flashin@" in env.get("DATABASE_URL", ""):
        invalid.append("DATABASE_URL uses development credentials")
    if env.get("POSTGRES_PASSWORD") == "flashin":
        invalid.append("POSTGRES_PASSWORD uses the development password")
    if not is_true(env.get("SCHEDULER_ENABLED")):
        invalid.append("SCHEDULER_ENABLED must be true in production")
    if not is_true(env.get("PILOT_RUNTIME_ENFORCED")):
        invalid.append("PILOT_RUNTIME_ENFORCED must be true in production")
    if "localhost" in env.get("CORS_ORIGINS", "") or "127.0.0.1" in env.get("CORS_ORIGINS", ""):
        invalid.append("CORS_ORIGINS contains a local address in production")

    for url_key in ("MINI_APP_URL", "API_PUBLIC_URL", "ADMIN_URL", "YOOKASSA_RETURN_URL"):
        require_https(env, url_key, invalid)

    if media_storage not in {"s3", "r2"}:
        invalid.append("MEDIA_STORAGE must be r2 or s3 in production")
    else:
        require_https(env, "MEDIA_PUBLIC_BASE_URL", invalid)

    if not is_true(env.get("MEILISEARCH_ENABLED")):
        invalid.append("MEILISEARCH_ENABLED must be true in production")

    if not env.get("MOYSKLAD_TOKEN") and not (
        env.get("MOYSKLAD_LOGIN") and env.get("MOYSKLAD_PASSWORD")
    ):
        invalid.append("Configure MOYSKLAD_TOKEN or MOYSKLAD_LOGIN and MOYSKLAD_PASSWORD")

    if not env.get("MOYSKLAD_SALE_PRICE_TYPE", "").strip():
        invalid.append("MOYSKLAD_SALE_PRICE_TYPE must identify the retail sale price")
    if not env.get("MOYSKLAD_SIZE_ATTRIBUTE_NAMES", "").strip():
        invalid.append("MOYSKLAD_SIZE_ATTRIBUTE_NAMES must not be empty")
    if not env.get("MOYSKLAD_COLOR_ATTRIBUTE_NAMES", "").strip():
        invalid.append("MOYSKLAD_COLOR_ATTRIBUTE_NAMES must not be empty")

batch_size = validate_int(env, "NOTIFICATION_BATCH_SIZE", 1, 200, invalid)
poll_seconds = validate_float(env, "NOTIFICATION_POLL_SECONDS", 1.0, 3600.0, invalid)
max_attempts = validate_int(env, "NOTIFICATION_MAX_ATTEMPTS", 1, 20, invalid)
initial_backoff = validate_int(env, "NOTIFICATION_INITIAL_BACKOFF_SECONDS", 5, 86400, invalid)
max_backoff = validate_int(env, "NOTIFICATION_MAX_BACKOFF_SECONDS", 5, 604800, invalid)
moysklad_interval = validate_int(env, "MOYSKLAD_SYNC_INTERVAL_MINUTES", 5, 1440, invalid)
provider_age = validate_int(env, "PILOT_PROVIDER_EVIDENCE_MAX_AGE_MINUTES", 5, 240, invalid)
live_age = validate_int(env, "PILOT_LIVE_GATE_MAX_AGE_MINUTES", 5, 120, invalid)
admission_age = validate_int(env, "PILOT_ADMISSION_MAX_AGE_MINUTES", 5, 240, invalid)
rollback_age = validate_int(env, "PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS", 1, 90, invalid)
pilot_runtime_max_orders = validate_int(env, "PILOT_RUNTIME_MAX_ORDERS", 1, 20, invalid)
if is_production and pilot_runtime_max_orders is not None and pilot_runtime_max_orders != 20:
    invalid.append("PILOT_RUNTIME_MAX_ORDERS must equal 20 in production")
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
        "moysklad_sync_interval_minutes": moysklad_interval,
        "pilot_provider_evidence_max_age_minutes": provider_age,
        "pilot_live_gate_max_age_minutes": live_age,
        "pilot_admission_max_age_minutes": admission_age,
        "pilot_rollback_drill_max_age_days": rollback_age,
        "pilot_runtime_enforced": is_true(env.get("PILOT_RUNTIME_ENFORCED")),
        "pilot_runtime_max_orders": pilot_runtime_max_orders,
    }
)
