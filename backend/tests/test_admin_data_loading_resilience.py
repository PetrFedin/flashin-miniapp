from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADMIN_SRC = ROOT / "admin" / "src"


def _read(name: str) -> str:
    return (ADMIN_SRC / name).read_text(encoding="utf-8")


def test_coordinator_covers_every_legacy_initial_admin_dataset():
    main = _read("main.jsx")
    coordinator = _read("admin-data-coordinator.js")
    expected_paths = {
        "/api/admin/products",
        "/api/admin/orders",
        "/api/admin/audit-logs",
        "/api/ops/inventory/low-stock",
        "/api/ops/abandoned-carts",
        "/api/support/admin/tickets",
        "/api/privacy/admin/requests",
        "/api/outbox",
        "/api/business-analytics/summary",
        "/api/crm/profiles",
        "/api/moysklad/sync-logs",
        "/api/campaigns",
        "/api/admin/customers",
        "/api/admin/moysklad/mapping-rules",
        "/api/admin/moysklad/conflicts",
        "/api/reconciliation/stock",
        "/api/fulfillment/tasks",
        "/api/fulfillment/sla",
        "/api/webhook-destinations",
    }

    assert len(expected_paths) == 19
    for path in expected_paths:
        assert f'api("{path}")' in main
        assert f'"{path}":' in coordinator


def test_coordinator_is_installed_before_legacy_admin_module_executes():
    bootstrap = _read("bootstrap.jsx")

    assert 'import "./main.jsx";' not in bootstrap
    assert "const dataCoordinator = installAdminDataCoordinator();" in bootstrap
    assert "const actionCoordinator = installAdminActionCoordinator();" in bootstrap
    assert 'await import("./main.jsx");' in bootstrap
    assert bootstrap.index("installAdminDataCoordinator()") < bootstrap.index("installAdminActionCoordinator()")
    assert bootstrap.index("installAdminActionCoordinator()") < bootstrap.index('await import("./main.jsx")')
    assert "bootstrap().catch" in bootstrap
    assert 'root.setAttribute("role", "alert")' in bootstrap


def test_known_get_wave_is_parallel_and_mutations_bypass_coordinator():
    source = _read("admin-data-coordinator.js")

    assert "for (const [path, definition] of Object.entries(DATASETS))" in source
    assert "const request = originalFetch" in source
    assert "`${API}${path}`" in source
    assert 'cache: "no-store"' in source
    assert "requests.set(path, request);" in source
    assert "await originalFetch" not in source
    assert 'requestMethod(input, init) !== "GET"' in source
    assert "return originalFetch(input, init);" in source
    assert "url.origin !== base.origin" in source
    assert "!(path in DATASETS)" in source


def test_partial_failures_return_typed_json_fallbacks_and_remain_visible():
    source = _read("admin-data-coordinator.js")
    status = _read("AdminRuntimeStatus.jsx")

    assert '"/api/business-analytics/summary": { label: "Аналитика", fallback: null }' in source
    assert '"/api/admin/products": { label: "Товары", fallback: [] }' in source
    assert '"X-Flashin-Data-Fallback": "1"' in source
    assert '"X-Flashin-Source-Status"' in source
    assert "return fallbackResponse(definition.fallback, response.status);" in source
    assert "return fallbackResponse(definition.fallback, 0);" in source
    assert "Часть разделов временно недоступна" in status
    assert "Остальные разделы загружены и продолжают работать независимо" in status


def test_new_wave_supersedes_stale_responses_without_cross_session_data():
    source = _read("admin-data-coordinator.js")

    assert "responseFromLatestBatch" in source
    assert "if (selectedBatch !== batch)" in source
    assert "currentToken !== token" in source
    assert 'throw new DOMException("Stale admin data request", "AbortError")' in source
    assert "selectedBatch = batch;" in source
    assert "response = await selectedBatch.requests.get(path);" in source


def test_unauthorized_wave_expires_session_once_and_reloads_clean_state():
    source = _read("admin-data-coordinator.js")
    bootstrap = _read("bootstrap.jsx")

    assert "let sessionExpired = false;" in source
    assert "if (sessionExpired) return;" in source
    assert 'localStorage.removeItem("admin_token")' in source
    assert "window.setTimeout(() => window.location.reload(), 0);" in source
    assert "window.addEventListener(sessionEvent, expire);" in bootstrap
    assert 'setToken("")' in bootstrap


def test_runtime_status_recovers_events_emitted_before_component_mount():
    source = _read("admin-data-coordinator.js")
    status = _read("AdminRuntimeStatus.jsx")

    assert "window.__flashinAdminDataStatus = detail;" in source
    assert "getStatus()" in source
    assert "normalizeDataStatus(window.__flashinAdminDataStatus)" in status
    assert "current.generation > next.generation" in status
    assert 'window.addEventListener("unhandledrejection", onUnhandledRejection)' in status
    assert 'aria-live="polite"' in status


def test_runtime_status_is_bounded_and_responsive():
    status = _read("AdminRuntimeStatus.jsx")
    styles = _read("admin-runtime-status.css")

    assert "MAX_MESSAGE = 1000" in status
    assert "detail.failures.slice(0, 50)" in status
    assert "Math.max(dataStatus.total, 1)" in status
    assert ".admin-runtime-status__warning" in styles
    assert ".admin-runtime-status__error" in styles
    assert "@media (max-width: 620px)" in styles
