import asyncio

import pytest
from fastapi import HTTPException

from backend.api.payments import _provider_order_id, _validated_provider_state
from backend.services import payments as payment_service


def _payment_payload(**overrides):
    payload = {
        "id": "pay-123",
        "status": "pending",
        "amount": {"value": "1250.40", "currency": "RUB"},
        "confirmation": {"confirmation_url": "https://example.test/confirm"},
        "metadata": {"order_id": "42"},
    }
    payload.update(overrides)
    return payload


def _refund_payload(**overrides):
    payload = {
        "id": "refund-123",
        "status": "pending",
        "payment_id": "pay-123",
        "amount": {"value": "250.40", "currency": "RUB"},
    }
    payload.update(overrides)
    return payload


def _error_code(exc: HTTPException) -> str:
    assert exc.status_code == 502
    assert isinstance(exc.detail, dict)
    return str(exc.detail.get("error"))


def test_create_payment_accepts_only_complete_matching_provider_contract(monkeypatch):
    async def fake_request(*args, **kwargs):
        return _payment_payload()

    monkeypatch.setattr(payment_service, "_request_yookassa", fake_request)

    result = asyncio.run(payment_service.create_yookassa_payment(42, 1250.4, "rub", attempt=2))

    assert result == {
        "provider_payment_id": "pay-123",
        "status": "pending",
        "confirmation_url": "https://example.test/confirm",
    }


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (_payment_payload(id=None), "invalid_payment_id"),
        (_payment_payload(status="unknown"), "invalid_payment_status"),
        (_payment_payload(amount={"value": "1250.41", "currency": "RUB"}), "payment_amount_mismatch"),
        (_payment_payload(amount={"value": "1250.40", "currency": "USD"}), "payment_amount_mismatch"),
        (_payment_payload(confirmation=[]), "invalid_confirmation"),
    ],
)
def test_create_payment_rejects_invalid_provider_contract(monkeypatch, payload, expected_error):
    async def fake_request(*args, **kwargs):
        return payload

    monkeypatch.setattr(payment_service, "_request_yookassa", fake_request)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(payment_service.create_yookassa_payment(42, 1250.4, "RUB"))

    assert _error_code(caught.value) == expected_error


def test_create_refund_validates_payment_link_amount_and_currency(monkeypatch):
    async def fake_request(*args, **kwargs):
        return _refund_payload(status="succeeded")

    monkeypatch.setattr(payment_service, "_request_yookassa", fake_request)

    result = asyncio.run(
        payment_service.create_yookassa_refund(
            "pay-123",
            250.4,
            "rub",
            order_id=42,
            refund_request_id=7,
        )
    )

    assert result == {
        "refund_id": "refund-123",
        "payment_id": "pay-123",
        "status": "succeeded",
        "amount": {"value": "250.40", "currency": "RUB"},
    }


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (_refund_payload(payment_id="pay-other"), "refund_payment_mismatch"),
        (_refund_payload(status="unknown"), "invalid_refund_status"),
        (_refund_payload(amount={"value": "250.41", "currency": "RUB"}), "refund_amount_mismatch"),
        (_refund_payload(amount={"value": "250.40", "currency": "USD"}), "refund_amount_mismatch"),
    ],
)
def test_create_refund_rejects_invalid_provider_contract(monkeypatch, payload, expected_error):
    async def fake_request(*args, **kwargs):
        return payload

    monkeypatch.setattr(payment_service, "_request_yookassa", fake_request)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            payment_service.create_yookassa_refund(
                "pay-123",
                250.4,
                "RUB",
                order_id=42,
                refund_request_id=7,
            )
        )

    assert _error_code(caught.value) == expected_error


def test_recovered_refund_requires_expected_refund_and_payment_ids():
    result = payment_service.validate_yookassa_refund(
        _refund_payload(status="succeeded"),
        "pay-123",
        250.4,
        "RUB",
        expected_refund_id="refund-123",
    )
    assert result["refund_id"] == "refund-123"
    assert result["payment_id"] == "pay-123"

    with pytest.raises(HTTPException) as mismatched_refund:
        payment_service.validate_yookassa_refund(
            _refund_payload(),
            "pay-123",
            250.4,
            "RUB",
            expected_refund_id="refund-other",
        )
    assert _error_code(mismatched_refund.value) == "refund_id_mismatch"


def test_webhook_transition_uses_current_provider_state_not_source_event_name():
    order_id, provider_status, effective_event = _validated_provider_state(
        _payment_payload(status="succeeded"),
        "pay-123",
    )

    assert order_id == 42
    assert provider_status == "succeeded"
    assert effective_event == "payment.succeeded"


def test_webhook_rejects_non_actionable_or_mismatched_provider_state():
    with pytest.raises(HTTPException) as pending:
        _validated_provider_state(_payment_payload(status="pending"), "pay-123")
    assert pending.value.status_code == 409

    with pytest.raises(HTTPException) as mismatched:
        _validated_provider_state(_payment_payload(id="pay-other", status="succeeded"), "pay-123")
    assert mismatched.value.status_code == 409

    with pytest.raises(HTTPException) as invalid_metadata:
        _provider_order_id(_payment_payload(metadata="42"))
    assert invalid_metadata.value.status_code == 409
