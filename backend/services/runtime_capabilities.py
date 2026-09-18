from __future__ import annotations

from fastapi import HTTPException

from ..config import Settings, get_settings


PAYMENT_MODES = frozenset({"disabled", "sandbox", "live"})
MOYSKLAD_MODES = frozenset({"disabled", "sandbox", "live"})


def payment_execution_enabled(settings: Settings | None = None) -> bool:
    runtime = settings or get_settings()
    return str(runtime.payments_mode or "").strip().lower() in {"sandbox", "live"}


def moysklad_execution_enabled(settings: Settings | None = None) -> bool:
    runtime = settings or get_settings()
    return str(runtime.moysklad_mode or "").strip().lower() in {"sandbox", "live"}


def require_commercial_checkout(settings: Settings | None = None) -> Settings:
    runtime = settings or get_settings()
    if not runtime.commercial_checkout_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "commercial_checkout_disabled",
                "message": "Commercial checkout is disabled for this production profile.",
            },
        )
    return runtime


def require_payment_execution(settings: Settings | None = None) -> Settings:
    runtime = settings or get_settings()
    if not payment_execution_enabled(runtime):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "payments_disabled",
                "message": "External payment execution is disabled for this production profile.",
            },
        )
    return runtime


def require_moysklad_execution(settings: Settings | None = None) -> Settings:
    runtime = settings or get_settings()
    if not moysklad_execution_enabled(runtime):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "moysklad_disabled",
                "message": "MoySklad external execution is disabled for this production profile.",
            },
        )
    return runtime


def public_runtime_capabilities(settings: Settings | None = None) -> dict[str, object]:
    runtime = settings or get_settings()
    payments_mode = str(runtime.payments_mode or "").strip().lower()
    moysklad_mode = str(runtime.moysklad_mode or "").strip().lower()
    return {
        "catalog": {"enabled": True},
        "cart": {"enabled": True},
        "commercial_checkout": {
            "enabled": bool(runtime.commercial_checkout_enabled),
        },
        "payments": {
            "enabled": payments_mode != "disabled",
            "mode": payments_mode,
            "provider": runtime.payment_provider if payments_mode != "disabled" else None,
        },
        "preorder": {"enabled": True},
        "made_to_order": {"enabled": True},
        "showroom": {"enabled": True},
        "moysklad": {
            "enabled": moysklad_mode != "disabled",
            "mode": moysklad_mode,
        },
        "search": {
            "enabled": True,
            "mode": "meilisearch" if runtime.meilisearch_enabled else "database",
        },
        "media": {
            "enabled": True,
            "mode": runtime.media_storage,
        },
        "controlled_commerce_pilot": {
            "enabled": bool(runtime.pilot_runtime_enforced),
            "max_orders": int(runtime.pilot_runtime_max_orders)
            if runtime.pilot_runtime_enforced
            else None,
        },
    }
