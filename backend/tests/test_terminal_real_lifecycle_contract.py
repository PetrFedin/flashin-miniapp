from pathlib import Path


def test_terminal_real_lifecycle_requires_one_delivered_refund_notification():
    source = (
        Path(__file__).resolve().parent / "e2e" / "test_order_payment_refund_flow.py"
    ).read_text(encoding="utf-8")

    assert "/api/admin/notification-delivery?status=sent&limit=200" in source
    assert "/api/admin/notifications" not in source
    assert "assert len(refund_notifications) == 1" in source
    assert 'refund_notification.get("status") == "sent"' in source
    assert 'refund_notification.get("sent_at")' in source
    assert '{"pending", "sent"}' not in source


def test_real_payment_runner_requires_explicit_controlled_variant():
    source = (
        Path(__file__).resolve().parent / "e2e" / "test_real_order_flow_runner.py"
    ).read_text(encoding="utf-8")

    assert '_required_int("E2E_VARIANT_ID")' in source
    assert "len(controlled_matches) == 1" in source
    assert 'variant.get("available_qty", 0) > 0' in source
    assert "products[0]" not in source
