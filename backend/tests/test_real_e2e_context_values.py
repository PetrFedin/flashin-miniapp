import runpy
from pathlib import Path

import pytest


RUNNER = Path(__file__).resolve().parent / "e2e" / "test_order_payment_refund_flow.py"


def _runner_globals():
    return runpy.run_path(str(RUNNER))


def test_terminal_context_accepts_zero_baseline_reservation():
    helper = _runner_globals()["_nonnegative_context_int"]

    assert helper({"baseline_reserved_qty": 0}, "baseline_reserved_qty") == 0
    assert helper({"baseline_reserved_qty": "0"}, "baseline_reserved_qty") == 0


@pytest.mark.parametrize("value", [-1, "-1", True, None, "invalid"])
def test_terminal_context_rejects_invalid_baseline_reservation(value):
    helper = _runner_globals()["_nonnegative_context_int"]

    with pytest.raises(AssertionError, match="non-negative integer"):
        helper({"baseline_reserved_qty": value}, "baseline_reserved_qty")


def test_terminal_context_positive_identifiers_remain_strict():
    helper = _runner_globals()["_positive_context_int"]

    assert helper({"order_id": 42}, "order_id") == 42
    with pytest.raises(AssertionError, match="positive integer"):
        helper({"order_id": 0}, "order_id")
