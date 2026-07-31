from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADMIN_SRC = ROOT / "admin" / "src"


def _read(name: str) -> str:
    return (ADMIN_SRC / name).read_text(encoding="utf-8")


def test_admin_bootstrap_installs_authenticated_data_exchange():
    bootstrap = _read("bootstrap.jsx")

    assert 'import { installAuthenticatedExportDownloads } from "./export-downloads";' in bootstrap
    assert "installAuthenticatedExportDownloads();" in bootstrap


def test_legacy_broken_data_exchange_controls_are_replaced_at_runtime():
    source = _read("export-downloads.js")

    assert 'LEGACY_EXPORT_PATH = "/api/admin/orders/export-csv"' in source
    assert 'querySelectorAll(`a[href*="${LEGACY_EXPORT_PATH}"]`)' in source
    assert 'section?.querySelector(\'input[type="file"][accept*=".csv"]\')' in source
    assert "legacyInput.remove();" in source
    assert "link.replaceWith(controls);" in source
    assert "MutationObserver" in source
    assert "window.open" not in source


def test_export_requests_use_authenticated_post_streaming_endpoints():
    source = _read("export-downloads.js")

    assert 'path: "/api/import-export/admin/export/orders"' in source
    assert 'path: "/api/import-export/admin/export/products"' in source
    assert 'method: "POST"' in source
    assert 'Authorization: `Bearer ${readAdminToken()}`' in source
    assert 'Accept: "text/csv"' in source
    assert 'localStorage.getItem("admin_token")' in source
    assert 'method: "GET"' not in source


def test_product_import_uses_bounded_authenticated_multipart_endpoint():
    source = _read("export-downloads.js")

    assert 'MAX_IMPORT_BYTES = 5_000_000' in source
    assert 'file.name.toLowerCase().endsWith(".csv")' in source
    assert "file.size > MAX_IMPORT_BYTES" in source
    assert 'form.append("file", file, file.name)' in source
    assert '`${API}/api/import-export/admin/products/import-csv`' in source
    assert 'headers: { Authorization: `Bearer ${readAdminToken()}` }' in source
    assert "body: form" in source
    assert "Content-Type" not in source
    assert 'contentType.includes("application/json")' in source


def test_download_validates_response_and_sanitizes_server_filename():
    source = _read("export-downloads.js")

    assert 'contentType.includes("text/csv")' in source
    assert 'response.headers.get("content-disposition")' in source
    assert "filename\\*" in source
    assert '.replace(/[\\\\/]/g, "_")' in source
    assert '.replace(/[\\u0000-\\u001f\\u007f]/g, "")' in source
    assert 'endsWith(".csv")' in source
    assert "if (!blob.size)" in source


def test_download_revokes_object_url_and_all_actions_are_fenced():
    source = _read("export-downloads.js")

    assert "URL.createObjectURL(blob)" in source
    assert "URL.revokeObjectURL(url)" in source
    assert "if (button.disabled) return;" in source
    assert "if (!file || importInput.disabled) return;" in source
    assert "setControlsDisabled(container, true);" in source
    assert "setControlsDisabled(container, false);" in source
    assert 'button.textContent = "Формирование…";' in source
    assert 'importInput.value = "";' in source


def test_data_exchange_feedback_is_accessible_and_responsive():
    source = _read("export-downloads.js")
    styles = _read("export-downloads.css")

    assert 'message.setAttribute("aria-live", "polite")' in source
    assert 'kind === "error" ? "alert" : "status"' in source
    assert ".admin-export-downloads__import" in styles
    assert ".admin-export-downloads__message--success" in styles
    assert ".admin-export-downloads__message--error" in styles
    assert "@media (max-width: 620px)" in styles
