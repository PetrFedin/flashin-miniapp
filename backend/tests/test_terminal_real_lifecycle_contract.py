from pathlib import Path


def test_terminal_real_lifecycle_requires_one_delivered_refund_notification():
    source = (
        Path(__file__).resolve().parent / "e2e" / "test_order_payment_refund_flow.py"
    ).read_text(encoding="utf-8")

    assert "assert len(refund_notifications) == 1" in source
    assert 'refund_notification.get("status") == "sent"' in source
    assert 'refund_notification.get("sent_at")' in source
    assert '{"pending", "sent"}' not in source
