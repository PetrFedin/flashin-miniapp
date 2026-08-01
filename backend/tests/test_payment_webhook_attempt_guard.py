from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_webhook_checks_latest_payment_before_canceling_order():
    source = (ROOT / "backend" / "api" / "payments.py").read_text(encoding="utf-8")

    assert "latest_order_payment" in source
    assert "is_stale_cancellation(" in source
    assert '"payment.cancellation_ignored"' in source
    assert source.index("is_stale_cancellation(") < source.index("cancel_order_before_settlement(", source.index("is_stale_cancellation("))
