from pathlib import Path


def test_terminal_real_lifecycle_requires_one_delivered_refund_notification():
    source = (
        Path(__file__).resolve().parent / "e2e" / "test_order_payment_refund_flow.py"
    ).read_text(encoding="utf-8")

    assert "/api/admin/notification-delivery?status=sent&limit=200" in source
    assert "/api/admin/notifications" not in source
    assert 'refund_event_key = f"order:{order_id}:refund:{return_id}:succeeded"' in source
    assert 'row.get("event_key") == refund_event_key' in source
    assert "assert len(refund_notifications) == 1" in source
    assert 'refund_notification.get("status") == "sent"' in source
    assert 'refund_notification.get("sent_at")' in source
    assert '{"pending", "sent"}' not in source


def test_terminal_real_lifecycle_requires_one_full_provider_refund():
    source = (
        Path(__file__).resolve().parent / "e2e" / "test_order_payment_refund_flow.py"
    ).read_text(encoding="utf-8")

    assert "assert len(completed_returns) == 1" in source
    assert 'return_request.get("refundable_balance", -1)' in source
    assert 'return_request.get("refunded_total", 0)' in source
    assert 'float(order["total_amount"])' in source


def test_terminal_stock_evidence_is_bound_to_controlled_order_item():
    source = (
        Path(__file__).resolve().parent / "e2e" / "test_order_payment_refund_flow.py"
    ).read_text(encoding="utf-8")

    assert 'assert len(order.get("items", [])) == 1' in source
    assert 'controlled_item.get("variant_id") == variant_id' in source
    assert 'controlled_item.get("quantity") == 1' in source


def test_real_payment_runner_requires_clean_controlled_cart_and_variant():
    source = (
        Path(__file__).resolve().parent / "e2e" / "test_real_order_flow_runner.py"
    ).read_text(encoding="utf-8")

    assert '_required_int("E2E_VARIANT_ID")' in source
    assert "len(controlled_matches) == 1" in source
    assert 'variant.get("available_qty", 0) > 0' in source
    assert "products[0]" not in source
    assert 'baseline_cart.get("items") == []' in source
    assert 'not baseline_cart.get("promo_code")' in source
    assert 'baseline_cart.get("loyalty_points_reserved")' in source
    assert 'len(controlled_cart.get("items", [])) == 1' in source
    assert 'order["items"][0].get("variant_id") == variant_id' in source
    assert 'order["items"][0].get("quantity") == 1' in source
