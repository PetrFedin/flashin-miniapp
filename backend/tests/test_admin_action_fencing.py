from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADMIN_SRC = ROOT / "admin" / "src"


def _read(name: str) -> str:
    return (ADMIN_SRC / name).read_text(encoding="utf-8")


def test_action_coordinator_is_installed_before_legacy_handlers():
    bootstrap = _read("bootstrap.jsx")

    assert 'import { installAdminActionCoordinator } from "./admin-action-coordinator";' in bootstrap
    assert "const dataCoordinator = installAdminDataCoordinator();" in bootstrap
    assert "const actionCoordinator = installAdminActionCoordinator();" in bootstrap
    assert 'await import("./main.jsx");' in bootstrap
    assert bootstrap.index("installAdminDataCoordinator()") < bootstrap.index("installAdminActionCoordinator()")
    assert bootstrap.index("installAdminActionCoordinator()") < bootstrap.index('await import("./main.jsx")')
    assert "actionCoordinator.sessionEvent || dataCoordinator.sessionEvent" in bootstrap


def test_only_same_origin_mutations_are_fenced():
    source = _read("admin-action-coordinator.js")

    assert 'new Set(["POST", "PUT", "PATCH", "DELETE"])' in source
    assert "!MUTATION_METHODS.has(method)" in source
    assert "url.origin !== base.origin" in source
    assert "return delegatedFetch(input, init);" in source
    assert 'requestMethod(input, init)' in source
    assert 'method || (input instanceof Request ? input.method : "GET")' in source


def test_identical_in_flight_requests_share_one_response_and_clone_it():
    source = _read("admin-action-coordinator.js")

    assert "const existing = inFlight.get(key);" in source
    assert "existing.duplicates += 1;" in source
    assert "const shared = await existing.promise;" in source
    assert "return shared.clone();" in source
    assert "record.promise = promise;" in source
    assert "inFlight.set(key, record);" in source
    assert "const response = await promise;" in source
    assert "return response.clone();" in source
    assert "inFlight.delete(key);" in source


def test_action_key_distinguishes_method_url_session_and_body_without_retries():
    source = _read("admin-action-coordinator.js")

    assert 'return [method, url.href, token, body].join("\\n");' in source
    assert "delegatedFetch(input, init)" in source
    assert source.count("delegatedFetch(input, init)") == 4
    assert "setTimeout" not in source.replace("window.setTimeout(() => window.location.reload(), 0);", "")
    assert "retry" not in source.lower()
    assert "while (" not in source


def test_fingerprints_cover_json_form_files_and_binary_bodies_safely():
    source = _read("admin-action-coordinator.js")

    assert "MAX_FINGERPRINT_BODY = 100_000" in source
    assert 'return `text:${body.slice(0, MAX_FINGERPRINT_BODY)}`;' in source
    assert "body instanceof URLSearchParams" in source
    assert "body instanceof FormData" in source
    assert "entries.sort();" in source
    assert "value instanceof File ? fileFingerprint(value) : String(value)" in source
    assert "file.name" in source
    assert "file.size" in source
    assert "file.type" in source
    assert "file.lastModified" in source
    assert "body instanceof Blob" in source
    assert "body instanceof ArrayBuffer" in source
    assert "ArrayBuffer.isView(body)" in source


def test_unfingerprintable_request_objects_bypass_deduplication():
    source = _read("admin-action-coordinator.js")

    assert 'input instanceof Request\n      ? "unfingerprintable"' in source
    assert 'if (body === "unfingerprintable") return null;' in source
    assert "if (!key) return delegatedFetch(input, init);" in source


def test_stale_session_responses_are_blocked_and_401_expires_once():
    source = _read("admin-action-coordinator.js")

    assert "let sessionExpired = false;" in source
    assert "if (sessionExpired) return;" in source
    assert 'localStorage.removeItem("admin_token")' in source
    assert "AUTH_TRANSITION_PATHS.has(url.pathname)" in source
    assert "currentToken !== token" in source
    assert 'throw new DOMException("Stale admin mutation response", "AbortError")' in source
    assert "window.setTimeout(() => window.location.reload(), 0);" in source


def test_action_status_persists_active_duplicate_and_terminal_outcomes():
    source = _read("admin-action-coordinator.js")

    assert "window.__flashinAdminActionStatus = detail;" in source
    assert "let lastAction = null;" in source
    assert "if (nextLast !== undefined) lastAction = nextLast;" in source
    assert 'type: "deduplicated"' in source
    assert 'type: "started"' in source
    assert 'type: response.ok ? "succeeded" : "failed"' in source
    assert "durationMs: Date.now() - startedAt" in source
    assert "duplicates: record.duplicates" in source
    assert "last: lastAction" in source


def test_runtime_status_shows_active_actions_and_suppressed_duplicates():
    status = _read("AdminRuntimeStatus.jsx")
    styles = _read("admin-runtime-status.css")

    assert 'ACTION_EVENT = "flashin-admin-action-status"' in status
    assert "normalizeActionStatus(window.__flashinAdminActionStatus)" in status
    assert 'window.addEventListener(ACTION_EVENT, onActionStatus)' in status
    assert "activeActions.length > 0" in status
    assert "Выполняются операции" in status
    assert "Повторных запросов подавлено" in status
    assert "actionFailureMessage" in status
    assert "<progress />" in status
    assert ".admin-runtime-status__actions" in styles
    assert "@media (max-width: 620px)" in styles


def test_coordinator_is_idempotent_and_restorable():
    source = _read("admin-action-coordinator.js")

    assert "if (window.__flashinAdminActionCoordinator)" in source
    assert "return window.__flashinAdminActionCoordinator;" in source
    assert "window.fetch = coordinatedFetch;" in source
    assert "window.fetch = delegatedFetch;" in source
    assert "inFlight.clear();" in source
    assert "window.__flashinAdminActionStatus = null;" in source
