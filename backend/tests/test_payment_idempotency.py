from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.payments import _provider_order_id, _validate_provider_amount
from backend.services.payments import _payment_idempotence_key, _refund_idempotence_key


def test_payment_idempotence_key_is_stable_per_attempt():
    first = _payment_idempotence_key(order_id=42, attempt=1)
    repeated = _payment_idempotence_key(order_id=42, attempt=1)
    next_attempt = _payment_idempotence_key(order_id=42, attempt=2)

    assert first == repeated
    assert first != next_attempt


def test_refund_idempotence_key_normalizes_amount():
    first = _refund_idempotence_key("payment-1", 42, 100, "RUB")
    repeated = _refund_idempotence_key("payment-1", 42, 100.0, "RUB")
    different_amount = _refund_idempotence_key("payment-1", 42, 101, "RUB")

    assert first == repeated
    assert first != different_amount


def test_provider_order_id_requires_positive_integer():
    assert _provider_order_id({"metadata": {"order_id": "42"}}) == 42

    for invalid in (None, "", "abc", "0", "-1"):
        with pytest.raises(HTTPException) as exc:
            _provider_order_id({"metadata": {"order_id": invalid}})
        assert exc.value.status_code == 409


def test_provider_amount_must_match_order_amount_and_currency():
    order = SimpleNamespace(total_amount=1250.0, currency="RUB")
    _validate_provider_amount({"amount": {"value": "1250.00", "currency": "RUB"}}, order)

    with pytest.raises(HTTPException) as amount_error:
        _validate_provider_amount({"amount": {"value": "1249.99", "currency": "RUB"}}, order)
    assert amount_error.value.status_code == 409

    with pytest.raises(HTTPException) as currency_error:
        _validate_provider_amount({"amount": {"value": "1250.00", "currency": "USD"}}, order)
    assert currency_error.value.status_code == 409


def test_provider_amount_rejects_invalid_payload():
    order = SimpleNamespace(total_amount=1250.0, currency="RUB")

    with pytest.raises(HTTPException) as exc:
        _validate_provider_amount({"amount": {"value": "not-a-number", "currency": "RUB"}}, order)

    assert exc.value.status_code == 409
