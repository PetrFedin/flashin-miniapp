from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADMIN_SRC = ROOT / "admin" / "src"


def test_admin_bootstrap_installs_runtime_order_workflow_boundary():
    bootstrap = (ADMIN_SRC / "bootstrap.jsx").read_text(encoding="utf-8")
    boundary = (ADMIN_SRC / "order-workflow-boundary.js").read_text(encoding="utf-8")

    assert 'import { installOrderWorkflowBoundary } from "./order-workflow-boundary";' in bootstrap
    assert "installOrderWorkflowBoundary();" in bootstrap
    assert 'row.querySelectorAll("select").forEach((select) => select.remove());' in boundary
    assert 'root.querySelectorAll(".row.order")' in boundary
    assert "MutationObserver" in boundary


def test_admin_ui_only_exposes_safe_pre_payment_cancellation():
    boundary = (ADMIN_SRC / "order-workflow-boundary.js").read_text(encoding="utf-8")

    assert 'status === "created" && paymentStatus === "pending"' in boundary
    assert "/api/admin/orders/${orderId}/cancel-safe" in boundary
    assert 'method: "POST"' in boundary
    assert "window.confirm" in boundary
    assert "if (button.disabled) return;" in boundary
    assert 'button.textContent = "Отмена…";' in boundary
    assert 'method: "PATCH"' not in boundary
    assert '`/api/admin/orders/${orderId}`' not in boundary


def test_admin_order_workflow_shows_inline_result_and_error_feedback():
    boundary = (ADMIN_SRC / "order-workflow-boundary.js").read_text(encoding="utf-8")
    styles = (ADMIN_SRC / "order-workflow-boundary.css").read_text(encoding="utf-8")

    assert 'role", kind === "error" ? "alert" : "status"' in boundary
    assert 'setMessage(row, `Заказ #${orderId} отменён безопасным workflow.`, "success")' in boundary
    assert 'setMessage(row, parseError(error), "error")' in boundary
    assert ".order-workflow-message--success" in styles
    assert ".order-workflow-message--error" in styles
    assert "@media (max-width: 620px)" in styles


def test_safe_admin_cancellation_route_checks_payment_flow_and_releases_dependencies():
    route_source = (
        ROOT
        / "backend"
        / "api"
        / "order_cancellation.py"
    ).read_text(encoding="utf-8")

    assert '@router.post("/admin/orders/{order_id}/cancel-safe"' in route_source
    assert "PaymentCreationAttempt" in route_source
    assert "payment_attempt_exists" in route_source
    assert "release_variant" in route_source
    assert "promo.used_count" in route_source
    assert 'hold.status = "released"' in route_source
    assert "queue_order_status" in route_source
