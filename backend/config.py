from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg2://flashin:flashin@db:5432/flashin"
    cors_origins: str = "http://localhost:5173,http://localhost:5174,https://mini.flashin.store,https://admin.flashin.store"
    telegram_bot_token: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 30
    admin_jwt_expire_minutes: int = 8 * 60

    admin_email: str = "admin@flashin.store"
    admin_password: str = "change-me-now"
    admin_totp_encryption_key: str = ""
    admin_mfa_required: bool = False
    admin_mfa_setup_token_minutes: int = 10
    admin_login_max_failures: int = 5
    admin_login_failure_window_minutes: int = 15
    admin_login_lockout_minutes: int = 15

    payment_provider: str = "yookassa"
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_return_url: str = "https://mini.flashin.store/payment-result"

    mini_app_url: str = "https://mini.flashin.store"
    api_public_url: str = "https://api.flashin.store"

    media_storage: str = "local"  # local | s3 | r2
    media_public_base_url: str = "http://localhost:8000/media"
    media_local_dir: str = "media"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    feature_flags_enabled: bool = True
    scheduler_enabled: bool = False
    media_generate_webp: bool = True
    media_generate_thumbnails: bool = True
    media_thumbnail_width: int = 480
    recommendation_personal_limit: int = 12
    import_export_dir: str = "exports"

    order_paid_to_assembling_sla_minutes: int = 120
    order_assembling_to_ready_sla_minutes: int = 1440

    outbox_signing_secret: str = "change-me-outbox-secret"

    meilisearch_enabled: bool = False
    meilisearch_url: str = "http://meilisearch:7700"
    meilisearch_master_key: str = "change-me"
    meilisearch_products_index: str = "products"

    referral_cookie_days: int = 30
    loyalty_max_redeem_percent: float = 30
    loyalty_point_value_rub: float = 1.0

    metrics_enabled: bool = True

    moysklad_base_url: str = "https://api.moysklad.ru/api/remap/1.2"
    moysklad_token: str = ""
    moysklad_login: str = ""
    moysklad_password: str = ""
    moysklad_default_currency: str = "RUB"
    moysklad_sync_limit: int = 100
    moysklad_sync_interval_minutes: int = 30

    cdn_public_base_url: str = "https://cdn.flashin.store"
    loyalty_points_per_ruble: float = 0.01
    sentry_dsn: str = ""
    abandoned_cart_minutes: int = 120
    inventory_low_stock_threshold: int = 2
    audit_log_enabled: bool = True
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    rate_limit_auth_per_minute: int = 20
    rate_limit_admin_login_per_minute: int = 10

    default_delivery_price: float = 0
    courier_delivery_price: float = 500
    pickup_delivery_price: float = 0

    enable_seed: bool = False
    use_create_all: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip().rstrip("/") for value in self.cors_origins.split(",") if value.strip()]

    @model_validator(mode="after")
    def validate_runtime_configuration(self):
        algorithm = self.jwt_algorithm.strip().upper()
        if algorithm not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM must be HS256, HS384, or HS512")
        self.jwt_algorithm = algorithm

        if not 1 <= self.jwt_expire_minutes <= 60 * 24 * 30:
            raise ValueError("JWT_EXPIRE_MINUTES must be between 1 and 43200")
        if not 1 <= self.admin_jwt_expire_minutes <= 60 * 24:
            raise ValueError("ADMIN_JWT_EXPIRE_MINUTES must be between 1 and 1440")
        if not 5 <= self.admin_mfa_setup_token_minutes <= 30:
            raise ValueError("ADMIN_MFA_SETUP_TOKEN_MINUTES must be between 5 and 30")
        if not 3 <= self.admin_login_max_failures <= 20:
            raise ValueError("ADMIN_LOGIN_MAX_FAILURES must be between 3 and 20")
        if not 1 <= self.admin_login_failure_window_minutes <= 60 * 24:
            raise ValueError("ADMIN_LOGIN_FAILURE_WINDOW_MINUTES must be between 1 and 1440")
        if not 1 <= self.admin_login_lockout_minutes <= 60 * 24:
            raise ValueError("ADMIN_LOGIN_LOCKOUT_MINUTES must be between 1 and 1440")
        if not 0 <= self.loyalty_max_redeem_percent <= 100:
            raise ValueError("LOYALTY_MAX_REDEEM_PERCENT must be between 0 and 100")
        if self.loyalty_point_value_rub <= 0 or self.loyalty_points_per_ruble < 0:
            raise ValueError("Loyalty rates must be valid")
        if min(
            self.rate_limit_per_minute,
            self.rate_limit_auth_per_minute,
            self.rate_limit_admin_login_per_minute,
            self.moysklad_sync_limit,
            self.moysklad_sync_interval_minutes,
        ) <= 0:
            raise ValueError("Rate limits and sync limits must be positive")
        if min(
            self.default_delivery_price,
            self.courier_delivery_price,
            self.pickup_delivery_price,
        ) < 0:
            raise ValueError("Delivery prices cannot be negative")
        if self.media_storage not in {"local", "s3", "r2"}:
            raise ValueError("MEDIA_STORAGE must be local, s3, or r2")

        if self.app_env.strip().lower() != "production":
            return self

        errors: list[str] = []
        weak_values = {
            "",
            "change-me",
            "change-me-now",
            "change-this-before-launch",
            "replace_with_long_random_secret",
            "replace_with_botfather_token",
            "test-secret",
            "test-token",
        }

        if len(self.jwt_secret) < 32 or self.jwt_secret.strip().lower() in weak_values:
            errors.append("JWT_SECRET must be a unique secret of at least 32 characters")
        if len(self.admin_password) < 12 or self.admin_password.strip().lower() in weak_values:
            errors.append("ADMIN_PASSWORD must be a strong non-default password")
        if (
            len(self.admin_totp_encryption_key) < 32
            or self.admin_totp_encryption_key.strip().lower() in weak_values
        ):
            errors.append("ADMIN_TOTP_ENCRYPTION_KEY must be a unique secret of at least 32 characters")
        elif hmac_compare_secret(self.admin_totp_encryption_key, self.jwt_secret):
            errors.append("ADMIN_TOTP_ENCRYPTION_KEY must differ from JWT_SECRET")
        if not self.admin_mfa_required:
            errors.append("ADMIN_MFA_REQUIRED must be true in production")
        if len(self.telegram_bot_token) < 20 or self.telegram_bot_token.strip().lower() in weak_values:
            errors.append("TELEGRAM_BOT_TOKEN is missing or unsafe")
        if len(self.outbox_signing_secret) < 32 or self.outbox_signing_secret.strip().lower() in weak_values:
            errors.append("OUTBOX_SIGNING_SECRET must be at least 32 characters")
        if self.payment_provider != "yookassa":
            errors.append("PAYMENT_PROVIDER must be yookassa")
        if not self.yookassa_shop_id.strip() or not self.yookassa_secret_key.strip():
            errors.append("YooKassa credentials are required")
        if not self.yookassa_return_url.startswith("https://"):
            errors.append("YOOKASSA_RETURN_URL must use HTTPS")
        if "flashin:flashin@" in self.database_url.lower():
            errors.append("DATABASE_URL still uses the default database password")
        if self.enable_seed or self.use_create_all:
            errors.append("ENABLE_SEED and USE_CREATE_ALL must be disabled in production")

        origins = self.cors_origin_list
        if not origins or any(origin == "*" or not origin.startswith("https://") for origin in origins):
            errors.append("CORS_ORIGINS must contain explicit HTTPS origins")

        if self.media_storage in {"s3", "r2"}:
            required_storage_values = (
                self.s3_endpoint_url,
                self.s3_bucket,
                self.s3_access_key_id,
                self.s3_secret_access_key,
            )
            if any(not value.strip() for value in required_storage_values):
                errors.append("S3/R2 storage credentials are incomplete")

        if self.meilisearch_enabled and (
            not self.meilisearch_master_key.strip()
            or self.meilisearch_master_key.strip().lower() in weak_values
        ):
            errors.append("MEILISEARCH_MASTER_KEY is missing or unsafe")

        if errors:
            raise ValueError("Unsafe production configuration: " + "; ".join(errors))
        return self


def hmac_compare_secret(first: str, second: str) -> bool:
    import hmac

    return hmac.compare_digest((first or "").encode("utf-8"), (second or "").encode("utf-8"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
