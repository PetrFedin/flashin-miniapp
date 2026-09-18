import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import orders as orders_api
from backend.api import payments as payments_api
from backend.api import returns as returns_api
from backend.config import Settings
from backend.jobs import moysklad_jobs, payment_jobs, provider_command_jobs, refund_jobs
from backend.services import moysklad as moysklad_service
from backend.services import payments as payments_service
from backend.services.runtime_capabilities import (
    public_runtime_capabilities,
    require_commercial_checkout,
    require_moysklad_execution,
    require_payment_execution,
)


def _disabled_settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "telegram_bot_token": "test-token",
        "jwt_secret": "test-secret",
        "commercial_checkout_enabled": False,
        "payments_mode": "disabled",
        "moysklad_mode": "disabled",
        "pilot_runtime_enforced": False,
        "yookassa_shop_id": "must-not-leak-shop",
        "yookassa_secret_key": "must-not-leak-secret",
        "moysklad_token": "must-not-leak-moysklad",
        "meilisearch_enabled": False,
        "media_storage": "local",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _raise_disabled(code: str):
    raise HTTPException(
        status_code=503,
        detail={"code": code, "message": "disabled by test"},
    )


def test_public_capability_projection_is_safe_and_explicit():
    settings = _disabled_settings()

    payload = public_runtime_capabilities(settings)

    assert payload["catalog"] == {"enabled": True}
    assert payload["cart"] == {"enabled": True}
    assert payload["commercial_checkout"] == {"enabled": False}
    assert payload["payments"]["enabled"] is False
    assert payload["payments"]["mode"] == "disabled"
    assert payload["payments"]["provider"] is None
    assert payload["moysklad"] == {"enabled": False, "mode": "disabled"}
    assert payload["preorder"] == {"enabled": True}
    assert payload["made_to_order"] == {"enabled": True}
    assert payload["showroom"] == {"enabled": True}
    assert payload["search"] == {"enabled": True, "mode": "database"}
    rendered = repr(payload)
    assert "must-not-leak-secret" not in rendered
    assert "must-not-leak-moysklad" not in rendered
    assert "must-not-leak-shop" not in rendered


@pytest.mark.parametrize(
    ("guard", "code"),
    [
        (require_commercial_checkout, "commercial_checkout_disabled"),
        (require_payment_execution, "payments_disabled"),
        (require_moysklad_execution, "moysklad_disabled"),
    ],
)
def test_disabled_capability_guards_fail_closed(guard, code):
    with pytest.raises(HTTPException) as exc_info:
        guard(_disabled_settings())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == code


def test_checkout_is_rejected_before_idempotency_or_database_mutation(monkeypatch):
    monkeypatch.setattr(
        orders_api,
        "require_commercial_checkout",
        lambda: _raise_disabled("commercial_checkout_disabled"),
    )
    monkeypatch.setattr(
        orders_api,
        "_normalize_idempotency_key",
        lambda *_args, **_kwargs: pytest.fail("checkout advanced past capability gate"),
    )

    with pytest.raises(HTTPException) as exc_info:
        orders_api.checkout(
            payload=object(),
            idempotency_key_header="not-reached",
            customer=SimpleNamespace(id=1),
            db=object(),
        )

    assert exc_info.value.detail["code"] == "commercial_checkout_disabled"


def test_payment_creation_is_rejected_before_attempt_claim(monkeypatch):
    monkeypatch.setattr(
        payments_api,
        "require_payment_execution",
        lambda: _raise_disabled("payments_disabled"),
    )
    monkeypatch.setattr(
        payments_api,
        "begin_payment_creation",
        lambda *_args, **_kwargs: pytest.fail("payment attempt was claimed while disabled"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            payments_api.create_payment(
                payload=SimpleNamespace(order_id=1),
                customer=SimpleNamespace(id=1),
                db=object(),
            )
        )

    assert exc_info.value.detail["code"] == "payments_disabled"


def test_payment_webhook_is_rejected_before_body_or_provider_io(monkeypatch):
    monkeypatch.setattr(
        payments_api,
        "require_payment_execution",
        lambda: _raise_disabled("payments_disabled"),
    )
    monkeypatch.setattr(
        payments_api,
        "fetch_yookassa_payment",
        lambda *_args, **_kwargs: pytest.fail("provider fetch ran while disabled"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(payments_api.yookassa_webhook(object(), db=object()))

    assert exc_info.value.detail["code"] == "payments_disabled"


def test_admin_refund_is_rejected_before_provider_or_refund_state(monkeypatch):
    monkeypatch.setattr(returns_api, "require_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        returns_api,
        "require_payment_execution",
        lambda: _raise_disabled("payments_disabled"),
    )
    monkeypatch.setattr(
        returns_api,
        "lock_return_request_for_approval",
        lambda *_args, **_kwargs: pytest.fail("refund state mutated while payments disabled"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            returns_api.approve_return(
                payload=SimpleNamespace(return_id=1, amount=None),
                admin=SimpleNamespace(id=1),
                db=object(),
            )
        )

    assert exc_info.value.detail["code"] == "payments_disabled"


def test_low_level_yookassa_transport_never_constructs_http_client_when_disabled(monkeypatch):
    settings = _disabled_settings()
    monkeypatch.setattr(payments_service, "get_settings", lambda: settings)

    def forbidden_client(*_args, **_kwargs):
        pytest.fail("YooKassa HTTP client constructed while payments are disabled")

    monkeypatch.setattr(payments_service.httpx, "AsyncClient", forbidden_client)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(payments_service._request_yookassa("GET", "/payments/provider-id"))

    assert exc_info.value.detail["code"] == "payments_disabled"


def test_low_level_moysklad_transport_never_constructs_http_client_when_disabled(monkeypatch):
    settings = _disabled_settings()
    monkeypatch.setattr(moysklad_service, "get_settings", lambda: settings)

    def forbidden_client(*_args, **_kwargs):
        pytest.fail("MoySklad HTTP client constructed while provider is disabled")

    monkeypatch.setattr(moysklad_service.httpx, "AsyncClient", forbidden_client)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(moysklad_service.fetch_assortment())

    assert exc_info.value.detail["code"] == "moysklad_disabled"


def test_disabled_background_jobs_do_not_touch_database_or_provider(monkeypatch):
    class ForbiddenDb:
        def __getattr__(self, _name):
            pytest.fail("disabled provider job touched the database")

    db = ForbiddenDb()
    monkeypatch.setattr(payment_jobs, "payment_execution_enabled", lambda: False)
    monkeypatch.setattr(refund_jobs, "payment_execution_enabled", lambda: False)
    monkeypatch.setattr(provider_command_jobs, "moysklad_execution_enabled", lambda: False)

    payment_result = asyncio.run(payment_jobs.reconcile_pending_payments(db))
    refund_result = asyncio.run(refund_jobs.reconcile_pending_refunds(db))
    moysklad_result = asyncio.run(provider_command_jobs.process_provider_commands(db))

    assert payment_result["seen"] == 0
    assert refund_result["seen"] == 0
    assert moysklad_result["claimed"] == 0


def test_disabled_scheduled_moysklad_pipeline_does_not_invoke_callbacks(monkeypatch):
    monkeypatch.setattr(moysklad_jobs, "moysklad_execution_enabled", lambda: False)

    async def forbidden_sync(*_args, **_kwargs):
        pytest.fail("MoySklad sync callback ran while provider was disabled")

    result = asyncio.run(
        moysklad_jobs.run_moysklad_pipeline(
            object(),
            sync_callback=forbidden_sync,
            crm_callback=lambda _db: pytest.fail("CRM rebuild ran"),
            recommendations_callback=lambda _db: pytest.fail("recommendation rebuild ran"),
        )
    )

    assert result["status"] == "disabled"
    assert result["products_seen"] == 0
