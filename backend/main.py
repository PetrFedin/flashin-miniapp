from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import catalog_intent_models as _catalog_intent_models  # noqa: F401
from . import catalog_models as _catalog_models  # noqa: F401
from . import checkout_models as _checkout_models  # noqa: F401
from . import model_constraints as _model_constraints  # noqa: F401
from . import notification_models as _notification_models  # noqa: F401
from . import provider_models as _provider_models  # noqa: F401
from .api.admin import router as admin_router
from .api.admin_auth import router as admin_auth_router
from .api.admin_notifications import router as admin_notifications_router
from .api.admin_promos import router as admin_promos_router
from .api.admin_returns import router as admin_returns_router
from .api.admin_security import router as admin_security_router
from .api.analytics import router as analytics_router
from .api.auth import router as auth_router
from .api.business_analytics import router as business_analytics_router
from .api.campaigns import router as campaigns_router
from .api.cart import router as cart_router
from .api.cart_items import router as cart_items_router
from .api.catalog_admin_operations import router as catalog_admin_operations_router
from .api.catalog_intents import router as catalog_intents_router
from .api.catalog_merchandising import router as catalog_merchandising_router
from .api.catalog_pricing import router as catalog_pricing_router
from .api.catalog_sharing import router as catalog_sharing_router
from .api.catalog_showroom import router as catalog_showroom_router
from .api.crm import router as crm_router
from .api.currency import router as currency_router
from .api.delivery import router as delivery_router
from .api.delivery_providers import router as delivery_providers_router
from .api.delivery_quotes import router as delivery_quotes_router
from .api.diagnostics import router as diagnostics_router
from .api.fulfillment import router as fulfillment_router
from .api.health import router as health_router
from .api.import_export import router as import_export_router
from .api.looks import router as looks_router
from .api.loyalty import router as loyalty_router
from .api.media import router as media_router
from .api.moysklad import router as moysklad_router
from .api.moysklad_deep_mapping import router as moysklad_deep_mapping_router
from .api.ops import router as ops_router
from .api.order_cancellation import router as order_cancellation_router
from .api.orders import router as orders_router
from .api.outbox import router as outbox_router
from .api.payment_reconciliation import router as payment_reconciliation_router
from .api.payments import router as payments_router
from .api.platform import router as platform_router
from .api.privacy import router as privacy_router
from .api.products import router as products_router
from .api.profile import router as profile_router
from .api.recommendations import router as recommendations_router
from .api.reconciliation import router as reconciliation_router
from .api.refund_webhooks import router as refund_webhooks_router
from .api.restock import router as restock_router
from .api.returns import router as returns_router
from .api.search import router as search_router
from .api.support import router as support_router
from .api.timeline import router as timeline_router
from .api.v1.router import router as v1_router
from .api.webhook_destinations import router as webhook_destinations_router
from .api.wishlist import router as wishlist_router
from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .middleware.admin_order_state_guard import AdminOrderStateGuardMiddleware
from .middleware.metrics import (
    MetricsMiddleware,
    collect_pilot_metrics,
    collect_provider_command_metrics,
    metrics_response,
)
from .middleware.rate_limit import InMemoryRateLimitMiddleware
from .middleware.request_id import RequestIdMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .seed import bootstrap_admin, seed_products
from .services.telegram_product_links import telegram_bot_username

settings = get_settings()
is_production = settings.app_env.strip().lower() == "production"

# The original rich-catalog module carried the first showroom implementation.
# Route these exact operations through the stricter UTC/fixed-slot boundary.
_REPLACED_CATALOG_SHOWROOM_ROUTES = {
    ("/catalog/showroom/appointments", "POST"),
    ("/catalog/showroom/appointments/me", "GET"),
    ("/catalog/admin/showroom/appointments", "GET"),
    ("/catalog/admin/showroom/appointments/{appointment_id}", "PATCH"),
}
catalog_merchandising_router.routes[:] = [
    route
    for route in catalog_merchandising_router.routes
    if not any(
        getattr(route, "path", "") == path
        and method in getattr(route, "methods", set())
        for path, method in _REPLACED_CATALOG_SHOWROOM_ROUTES
    )
]

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,
        send_default_pii=False,
        environment=settings.app_env,
    )


def initialize_application() -> None:
    if is_production and not telegram_bot_username():
        raise RuntimeError("TELEGRAM_BOT_USERNAME must be configured in production")
    if settings.use_create_all:
        Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        bootstrap_admin(db)
        if settings.enable_seed:
            seed_products(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_application()
    yield


app = FastAPI(
    title="FLASHIN Mini App Backend v54",
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(InMemoryRateLimitMiddleware)
app.add_middleware(AdminOrderStateGuardMiddleware)
if settings.metrics_enabled:
    app.add_middleware(MetricsMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
# Added last so the correlation id wraps CORS, rate-limit, guard and route responses.
app.add_middleware(RequestIdMiddleware)

Path(settings.media_local_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_local_dir), name="media")

app.include_router(health_router)
app.include_router(currency_router, prefix="/currency")
app.include_router(auth_router, prefix="/api")
app.include_router(products_router, prefix="/api")
app.include_router(catalog_merchandising_router, prefix="/api")
app.include_router(catalog_pricing_router, prefix="/api")
app.include_router(catalog_showroom_router, prefix="/api")
app.include_router(catalog_sharing_router, prefix="/api")
app.include_router(catalog_admin_operations_router, prefix="/api")
app.include_router(catalog_intents_router, prefix="/api")
app.include_router(cart_router, prefix="/api")
app.include_router(cart_items_router, prefix="/api")
app.include_router(order_cancellation_router, prefix="/api")
app.include_router(orders_router, prefix="/api")
app.include_router(payments_router, prefix="/api")
app.include_router(refund_webhooks_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(admin_auth_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(admin_promos_router, prefix="/api")
app.include_router(admin_notifications_router, prefix="/api")
app.include_router(admin_returns_router, prefix="/api")
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


if settings.metrics_enabled:

    @app.get("/metrics", include_in_schema=False)
    def metrics(db: Session = Depends(get_db)):
        collect_pilot_metrics(db, settings)
        collect_provider_command_metrics(db)
        return metrics_response()


@app.get("/", include_in_schema=not is_production)
def root():
    return {"message": "FLASHIN Mini App API v54", "env": settings.app_env}
