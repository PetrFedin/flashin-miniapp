from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import payments as payment_api
from backend.api import returns as returns_api
from backend.services import pilot_circuit_breaker as circuit


class BrokenDatabase:
    def __init__(self):
        self.rolled_back = False

    def query(self, *_args, **_kwargs):
        raise RuntimeError("database unavailable")

    def rollback(self):
        self.rolled_back = True


def test_payment_integrity_error_preserves_http_contract_and_machine_reason():
    error = payment_api.ProviderPaymentIntegrityError(
        "provider_payment_id_invalid",
        "Payment provider returned an invalid payment id",
        status_code=502,
    )

    assert isinstance(error, HTTPException)
    assert error.status_code == 502
    assert error.reason == "provider_payment_id_invalid"
    assert error.detail == "Payment provider returned an invalid payment id"


def test_payment_trip_preserves_original_status_when_safety_stop_succeeds(monkeypatch):
    monkeypatch.setattr(
        payment_api,
        "trip_pilot_circuit_breaker",
        lambda **_kwargs: circuit.PilotCircuitTrip(False, False, None),
    )
    error = payment_api.ProviderPaymentIntegrityError(
        "provider_payment_id_invalid",
        "Payment provider returned an invalid payment id",
        status_code=502,
    )

    response = payment_api._trip_after_rollback(10, error)

    assert response.status_code == 502
    assert response.detail == error.detail


def test_provider_amount_rejects_non_finite_values():
    order = SimpleNamespace(total_amount=100, currency="RUB")

    for value in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(HTTPException) as exc:
            payment_api._validate_provider_amount(
                {"amount": {"value": value, "currency": "RUB"}},
                order,
            )
        assert exc.value.status_code == 409


def test_refund_retry_state_failure_is_not_silently_swallowed():
    db = BrokenDatabase()

    with pytest.raises(HTTPException) as exc:
        returns_api._mark_retry_required(db, return_id=1, order_id=1)

    assert exc.value.status_code == 503
    assert db.rolled_back is True


def test_refund_review_state_failure_is_not_silently_swallowed():
    db = BrokenDatabase()

    with pytest.raises(HTTPException) as exc:
        returns_api._mark_review_required(
            db,
            return_id=1,
            order_id=1,
            provider_refund_id="refund-1",
        )

    assert exc.value.status_code == 503
    assert db.rolled_back is True
