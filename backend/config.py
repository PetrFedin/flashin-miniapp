from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg2://flashin:flashin@db:5432/flashin"
    cors_origins: str = "http://localhost:5173,http://localhost:5174,https://mini.flashin.store,https://admin.flashin.store"
    telegram_bot_token: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 30

    admin_email: str = "admin@flashin.store"
    admin_password: str = "change-me-now"

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

    # v41 platform
    feature_flags_enabled: bool = True
    scheduler_enabled: bool = False
    media_generate_webp: bool = True
    media_generate_thumbnails: bool = True
    media_thumbnail_width: int = 480
    recommendation_personal_limit: int = 12
    import_export_dir: str = "exports"
    # Fulfillment / SLA
    order_paid_to_assembling_sla_minutes: int = 120
    order_assembling_to_ready_sla_minutes: int = 1440

    # Outbox signing
    outbox_signing_secret: str = "change-me-outbox-secret"
    # Search
    meilisearch_enabled: bool = False
    meilisearch_url: str = "http://meilisearch:7700"
    meilisearch_master_key: str = "change-me"
    meilisearch_products_index: str = "products"

    # Referral and loyalty
    referral_cookie_days: int = 30
    loyalty_max_redeem_percent: float = 30
    loyalty_point_value_rub: float = 1.0

    # Metrics
    metrics_enabled: bool = True
    # MoySklad integration
    moysklad_base_url: str = "https://api.moysklad.ru/api/remap/1.2"
    moysklad_token: str = ""
    moysklad_login: str = ""
    moysklad_password: str = ""
    moysklad_default_currency: str = "RUB"
    moysklad_sync_limit: int = 100
    moysklad_sync_interval_minutes: int = 30

    # CDN / analytics
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
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
