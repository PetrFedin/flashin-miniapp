import logging
import time
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from .middleware.rate_limit import InMemoryRateLimitMiddleware
from .middleware.metrics import MetricsMiddleware, metrics_response
from .middleware.security_headers import SecurityHeadersMiddleware
from .middleware.request_context import RequestContextMiddleware
from .middleware.request_body_limit import RequestBodyLimitMiddleware
from .api.analytics import router as analytics_router
from .api.auth import router as auth_router
from .api.cart import router as cart_router
from .api.currency import router as currency_router
from .api.health import router as health_router
from .api.orders import router as orders_router
from .api.payments import router as payments_router
from .api.products import router as products_router
from .api.admin import router as admin_router
from .api.media import router as media_router
from .api.returns import router as returns_router
from .api.wishlist import router as wishlist_router
from .api.restock import router as restock_router
from .api.delivery import router as delivery_router
from .api.ops import router as ops_router
from .api.support import router as support_router
from .api.privacy import router as privacy_router
from .api.outbox import router as outbox_router
from .api.moysklad import router as moysklad_router
from .api.crm import router as crm_router
from .api.recommendations import router as recommendations_router
from .api.business_analytics import router as business_analytics_router
from .api.loyalty import router as loyalty_router
from .api.campaigns import router as campaigns_router
from .api.search import router as search_router
from .api.looks import router as looks_router
from .api.timeline import router as timeline_router
from .api.reconciliation import router as reconciliation_router
from .api.fulfillment import router as fulfillment_router
from .api.webhook_destinations import router as webhook_destinations_router
from .api.profile import router as profile_router
from .api.platform import router as platform_router
from .api.import_export import router as import_export_router
from .api.v1.router import router as v1_router
from .api.diagnostics import router as diagnostics_router
from .api.payment_reconciliation import router as payment_reconciliation_router
from .api.delivery_providers import router as delivery_providers_router
from .api.moysklad_deep_mapping import router as moysklad_deep_mapping_router
from .api.admin_security import router as admin_security_router
from .api.delivery_quotes import router as delivery_quotes_router
from .api.catalog_management import router as catalog_management_router
from .api.enterprise import router as enterprise_router
from .api.telegram_commerce import router as telegram_commerce_router
from .api.telegram_webhook import router as telegram_webhook_router
from .api.fashion_ai import router as fashion_ai_router
from .config import get_settings
from .database import Base, SessionLocal, engine
from .error_handlers import register_error_handlers
from .seed import bootstrap_admin, seed_products

logger = logging.getLogger(__name__)
settings = get_settings()
if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)

app = FastAPI(title="FLASHIN Mini App Backend v52")
register_error_handlers(app)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(InMemoryRateLimitMiddleware)
if settings.metrics_enabled:
    app.add_middleware(MetricsMiddleware)

# Added before CORS and request context so the runtime order is:
# RequestContext -> CORS -> RequestBodyLimit -> remaining application stack.
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=settings.max_request_body_bytes,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)

Path(settings.media_local_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_local_dir), name="media")

app.include_router(health_router)
app.include_router(currency_router, prefix="/currency")
app.include_router(auth_router, prefix="/api")
app.include_router(products_router, prefix="/api")
app.include_router(cart_router, prefix="/api")
app.include_router(orders_router, prefix="/api")
app.include_router(payments_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(media_router, prefix="/api")
app.include_router(returns_router, prefix="/api")
app.include_router(wishlist_router, prefix="/api")
app.include_router(restock_router, prefix="/api")
app.include_router(delivery_router, prefix="/api")
app.include_router(ops_router, prefix="/api")
app.include_router(support_router, prefix="/api")
app.include_router(privacy_router, prefix="/api")
app.include_router(outbox_router, prefix="/api")
app.include_router(moysklad_router, prefix="/api")
app.include_router(crm_router, prefix="/api")
app.include_router(recommendations_router, prefix="/api")
app.include_router(business_analytics_router, prefix="/api")
app.include_router(loyalty_router, prefix="/api")
app.include_router(campaigns_router, prefix="/api")
app.include_router(search_router, prefix="/api")
app.include_router(looks_router, prefix="/api")
app.include_router(timeline_router, prefix="/api")
app.include_router(reconciliation_router, prefix="/api")
app.include_router(fulfillment_router, prefix="/api")
app.include_router(webhook_destinations_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(platform_router, prefix="/api")
app.include_router(import_export_router, prefix="/api")
app.include_router(v1_router, prefix="/api")
app.include_router(diagnostics_router, prefix="/api")
app.include_router(payment_reconciliation_router, prefix="/api")
app.include_router(delivery_providers_router, prefix="/api")
app.include_router(moysklad_deep_mapping_router, prefix="/api")
app.include_router(admin_security_router, prefix="/api")
app.include_router(delivery_quotes_router, prefix="/api")
app.include_router(catalog_management_router, prefix="/api")
app.include_router(enterprise_router, prefix="/api")
app.include_router(telegram_commerce_router, prefix="/api")
app.include_router(telegram_webhook_router, prefix="/api")
app.include_router(fashion_ai_router, prefix="/api")


def _bootstrap_database(max_attempts: int = 10, base_delay_seconds: float = 1.0) -> None:
    """Initialize startup data with retries for transient Docker DNS/database failures."""
    last_error: OperationalError | None = None

    for attempt in range(1, max_attempts + 1):
        db = SessionLocal()
        try:
            if settings.use_create_all:
                Base.metadata.create_all(bind=engine)
            bootstrap_admin(db)
            if settings.enable_seed:
                seed_products(db)
            if attempt > 1:
                logger.info("Database startup recovered on attempt %s", attempt)
            return
        except OperationalError as exc:
            db.rollback()
            last_error = exc
            if attempt == max_attempts:
                break
            delay = min(base_delay_seconds * attempt, 5.0)
            logger.warning(
                "Database unavailable during startup (attempt %s/%s). Retrying in %.1fs: %s",
                attempt,
                max_attempts,
                delay,
                exc,
            )
            time.sleep(delay)
        finally:
            db.close()

    logger.error("Database startup failed after %s attempts", max_attempts)
    if last_error is not None:
        raise last_error


@app.on_event("startup")
def on_startup():
    _bootstrap_database()


@app.get("/metrics")
def metrics():
    return metrics_response()


@app.get("/")
def root():
    return {"message": "FLASHIN Mini App API v52", "env": settings.app_env}
