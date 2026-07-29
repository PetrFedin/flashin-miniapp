from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import refund_state


def _order(total=1000.0):
    return SimpleNamespace(
        id=42,
        customer_id=7,
        total_amount=total,
        currency="RUB",
        loyalty_points_redeemed=100.0,
        status="refund_requested",
        payment_status="refund_processing",
    )


def _return(amount=1000.0):
    return SimpleNamespace(refund_amount=amount, status="processing")


def test_refund_money_rejects_non_finite_values():
    assert refund_state.refund_money("100.005", "amount").as_tuple().exponent == -2

    for invalid in ("not-a-number", float("nan"), float("inf")):
        with pytest.raises(HTTPException) as exc:
            refund_state.refund_money(invalid, "amount")
        assert exc.value.status_code == 400


def test_provider_refund_amount_validates_currency():
    assert refund_state.provider_refund_amount(
        {"amount": {"value": "1000.00", "currency": "RUB"}},
        "RUB",
    ) == refund_state.refund_money(1000, "amount")

    with pytest.raises(HTTPException) as exc:
        refund_state.provider_refund_amount(
            {"amount": {"value": "1000.00", "currency": "USD"}},
            "RUB",
        )
    assert exc.value.status_code == 409


def test_full_refund_applies_loyalty_reversal(monkeypatch):
    called = {}

    def fake_apply(db, *, customer_id, order_id, redeemed_points):
        called.update(
            customer_id=customer_id,
            order_id=order_id,
            redeemed_points=redeemed_points,
        )
        return {"ok": True}

    monkeypatch.setattr(refund_state, "apply_full_refund_loyalty", fake_apply)
    order = _order()
    ret = _return()

    result = refund_state.apply_provider_refund_status(object(), ret, order, "succeeded")

    assert ret.status == "approved"
    assert order.status == "refunded"
    assert order.payment_status == "refunded"
    assert called == {"customer_id": 7, "order_id": 42, "redeemed_points": 100.0}
    assert result == {"ok": True}


def test_partial_refund_does_not_silently_recalculate_loyalty(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("full-refund loyalty handler must not run")

    monkeypatch.setattr(refund_state, "apply_full_refund_loyalty", fail_if_called)
    order = _order()
    ret = _return(250.0)

    result = refund_state.apply_provider_refund_status(object(), ret, order, "succeeded")

    assert ret.status == "approved_partial"
    assert order.status == "partially_refunded"
    assert order.payment_status == "partially_refunded"
    assert result["policy"] == "no_automatic_loyalty_adjustment_for_partial_refund"


def test_canceled_and_pending_refunds_preserve_reconcilable_states():
    canceled_order = _order()
    canceled_return = _return()
    refund_state.apply_provider_refund_status(object(), canceled_return, canceled_order, "canceled")
    assert canceled_return.status == "failed"
    assert canceled_order.payment_status == "paid"

    pending_order = _order()
    pending_return = _return()
    refund_state.apply_provider_refund_status(object(), pending_return, pending_order, "pending")
    assert pending_return.status == "refund_pending"
    assert pending_order.status == "refund_requested"
    assert pending_order.payment_status == "refund_pending"
