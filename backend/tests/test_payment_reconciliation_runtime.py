import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import payments as payments_api


def make_order():
    return SimpleNamespace(id=7, total_amount=1250.0, currency="RUB")


def make_payment(**overrides):
    values = {
        "provider_payment_id": "pay-7",
        "status": "pending",
        "confirmation_url": "https://pay.example/old",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def provider_payment(status, confirmation_url=None):
    data = {
        "id": "pay-7",
        "status": status,
        "metadata": {"order_id": "7"},
        "amount": {"value": "1250.00", "currency": "RUB"},
    }
    if confirmation_url is not None:
        data["confirmation"] = {"confirmation_url": confirmation_url}
    return data


def test_pending_attempt_refreshes_provider_confirmation_url(monkeypatch):
    async def fetch(_payment_id):
        return provider_payment("pending", "https://pay.example/new")

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)
    payment = make_payment()

    result = asyncio.run(payments_api._reconcile_existing_payment(make_order(), payment))

    assert result is payment
    assert payment.status == "pending"
    assert payment.confirmation_url == "https://pay.example/new"


def test_canceled_provider_attempt_is_replaced(monkeypatch):
    async def fetch(_payment_id):
        return provider_payment("canceled")

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)
    payment = make_payment()

    result = asyncio.run(payments_api._reconcile_existing_payment(make_order(), payment))

    assert result is None
    assert payment.status == "canceled"
    assert payment.confirmation_url == ""


def test_succeeded_provider_attempt_is_not_duplicated(monkeypatch):
    async def fetch(_payment_id):
        return provider_payment("succeeded", "https://pay.example/stale")

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)
    payment = make_payment()

    result = asyncio.run(payments_api._reconcile_existing_payment(make_order(), payment))

    assert result is payment
    assert payment.status == "succeeded"
    assert payment.confirmation_url == ""


def test_provider_outage_falls_back_only_to_existing_active_link(monkeypatch):
    async def fail(_payment_id):
        raise HTTPException(status_code=502, detail="provider offline")

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fail)
    payment = make_payment()

    result = asyncio.run(payments_api._reconcile_existing_payment(make_order(), payment))

    assert result is payment
    assert payment.confirmation_url == "https://pay.example/old"


def test_provider_outage_without_active_link_is_not_hidden(monkeypatch):
    async def fail(_payment_id):
        raise HTTPException(status_code=502, detail="provider offline")

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fail)
    payment = make_payment(confirmation_url="")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(payments_api._reconcile_existing_payment(make_order(), payment))

    assert exc_info.value.status_code == 502


def test_active_provider_attempt_without_any_confirmation_url_is_blocked(monkeypatch):
    async def fetch(_payment_id):
        return provider_payment("pending")

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)
    payment = make_payment(confirmation_url="")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(payments_api._reconcile_existing_payment(make_order(), payment))

    assert exc_info.value.status_code == 409
    assert "confirmation URL" in exc_info.value.detail


def test_provider_order_reference_mismatch_is_rejected(monkeypatch):
    async def fetch(_payment_id):
        data = provider_payment("pending", "https://pay.example/new")
        data["metadata"]["order_id"] = "8"
        return data

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(payments_api._reconcile_existing_payment(make_order(), make_payment()))

    assert exc_info.value.status_code == 409
    assert "another order" in exc_info.value.detail
