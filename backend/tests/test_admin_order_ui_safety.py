from pathlib import Path


def test_admin_ui_does_not_expose_generic_order_status_selector():
    admin_root = Path(__file__).resolve().parents[2] / "admin" / "src"
    source = (admin_root / "main.jsx").read_text(encoding="utf-8")
    styles = (admin_root / "style.css").read_text(encoding="utf-8")

    assert '<select value={o.status}' in source
    assert ".order select { display: none; }" in styles
    assert "Статус управляется оплатой, сборкой, доставкой или возвратом" in styles


def test_safe_admin_cancellation_route_remains_available():
    route_source = (
        Path(__file__).resolve().parents[1]
        / "api"
        / "order_cancellation.py"
    ).read_text(encoding="utf-8")

    assert '@router.post("/admin/orders/{order_id}/cancel-safe"' in route_source
    assert "PaymentCreationAttempt" in route_source
    assert "payment_attempt_exists" in route_source
