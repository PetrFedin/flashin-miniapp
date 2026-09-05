from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import payments as payments_api
from backend.services.payment_attempts import can_fallback_to_stored_attempt


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


def test_pending_attempt_refreshes_provider_confirmation_url():
    payment = make_payment()

    result = payments_api._apply_provider_reconciliation(
        make_order(),
        payment,
        provider_payment("pending", "https://pay.example/new"),
    )

    assert result is payment
    assert payment.status == "pending"
    assert payment.confirmation_url == "https://pay.example/new"


def test_canceled_provider_attempt_is_replaced():
    payment = make_payment()

    result = payments_api._apply_provider_reconciliation(
        make_order(),
        payment,
        provider_payment("canceled"),
    )

    assert result is None
    assert payment.status == "canceled"
    assert payment.confirmation_url == ""


def test_succeeded_provider_attempt_is_not_duplicated():
    payment = make_payment()

    result = payments_api._apply_provider_reconciliation(
        make_order(),
        payment,
        provider_payment("succeeded", "https://pay.example/stale"),
    )

    assert result is payment
    assert payment.status == "succeeded"
    assert payment.confirmation_url == ""


def test_provider_outage_falls_back_only_to_existing_active_link():
    payment = make_payment()

    assert can_fallback_to_stored_attempt(payment.status, payment.confirmation_url) is True


def test_provider_outage_without_active_link_is_not_hidden():
    payment = make_payment(confirmation_url="")

    assert can_fallback_to_stored_attempt(payment.status, payment.confirmation_url) is False


def test_active_provider_attempt_without_any_confirmation_url_is_blocked():
    payment = make_payment(confirmation_url="")

    with pytest.raises(HTTPException) as exc_info:
        payments_api._apply_provider_reconciliation(
            make_order(),
            payment,
            provider_payment("pending"),
        )

    assert exc_info.value.status_code == 409
    assert "confirmation URL" in exc_info.value.detail


def test_provider_order_reference_mismatch_is_rejected():
    data = provider_payment("pending", "https://pay.example/new")
    data["metadata"]["order_id"] = "8"

    with pytest.raises(HTTPException) as exc_info:
        payments_api._apply_provider_reconciliation(make_order(), make_payment(), data)

    assert exc_info.value.status_code == 409
    assert "another order" in exc_info.value.detail
