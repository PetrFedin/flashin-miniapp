import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_readiness import (  # noqa: E402
    build_report,
    check_distinct_hosts,
    check_env_exact,
    check_http,
    check_public_https_url,
)


SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


class Handler(BaseHTTPRequestHandler):
    routes = {
        "/health": (200, "application/json", {"status": "ok"}, SECURITY_HEADERS),
        "/ready": (
            200,
            "application/json",
            {"status": "ready", "database": "ok", "migrations": "current"},
            SECURITY_HEADERS,
        ),
        "/bad-ready": (
            200,
            "application/json",
            {"status": "ready", "database": "down", "migrations": "current"},
            SECURITY_HEADERS,
        ),
        "/products": (200, "application/json", [{"id": 1}], SECURITY_HEADERS),
        "/empty-products": (200, "application/json", [], SECURITY_HEADERS),
        "/admin": (200, "text/html; charset=utf-8", "<html>" + "a" * 150 + "</html>", SECURITY_HEADERS),
        "/weak-admin": (200, "text/html; charset=utf-8", "<html>" + "a" * 150 + "</html>", {}),
    }

    def do_GET(self):
        status, content_type, payload, headers = self.routes.get(
            self.path, (404, "application/json", {"detail": "not found"}, SECURITY_HEADERS)
        )
        body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_public_https_url_accepts_public_base():
    assert check_public_https_url("url", "https://api.flashin.store").ok
    assert check_public_https_url("url", "https://api.flashin.store/").ok


def test_public_https_url_rejects_unsafe_or_non_base_values():
    invalid = (
        "http://api.flashin.store",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.2",
        "https://user:pass@api.flashin.store",
        "https://api.flashin.store/path",
        "https://api.flashin.store?debug=1",
        "https://api.flashin.store:8443",
        "https://api.internal",
    )
    for value in invalid:
        assert not check_public_https_url("url", value).ok, value


def test_public_hosts_must_be_distinct():
    valid = check_distinct_hosts(
        "hosts",
        {
            "api": "https://api.flashin.store",
            "mini": "https://mini.flashin.store",
            "admin": "https://admin.flashin.store",
        },
    )
    duplicate = check_distinct_hosts(
        "hosts",
        {
            "api": "https://api.flashin.store",
            "mini": "https://api.flashin.store",
            "admin": "https://admin.flashin.store",
        },
    )
    assert valid.ok
    assert not duplicate.ok
    assert "duplicate hosts" in duplicate.detail


def test_environment_must_be_explicitly_production():
    assert check_env_exact("env", "production", "production").ok
    assert not check_env_exact("env", "development", "production").ok
    assert not check_env_exact("env", None, "production").ok


def test_http_json_semantics_and_non_empty_catalog():
    server, base = server_url()
    try:
        health = check_http(
            "health",
            f"{base}/health",
            expected_content_type="application/json",
            expected_json={"status": "ok"},
        )
        ready = check_http(
            "ready",
            f"{base}/ready",
            expected_content_type="application/json",
            expected_json={"status": "ready", "database": "ok", "migrations": "current"},
        )
        bad_ready = check_http(
            "ready",
            f"{base}/bad-ready",
            expected_content_type="application/json",
            expected_json={"status": "ready", "database": "ok", "migrations": "current"},
        )
        catalog = check_http(
            "catalog",
            f"{base}/products",
            expected_content_type="application/json",
            expected_json_type=list,
            require_non_empty_json=True,
        )
        empty_catalog = check_http(
            "catalog",
            f"{base}/empty-products",
            expected_content_type="application/json",
            expected_json_type=list,
            require_non_empty_json=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert health.ok
    assert ready.ok
    assert not bad_ready.ok
    assert catalog.ok
    assert not empty_catalog.ok
    assert "JSON payload is empty" in empty_catalog.detail


def test_http_security_headers_are_enforced():
    server, base = server_url()
    required = {
        "strict-transport-security": "max-age=",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
        "x-frame-options": "deny",
        "cache-control": "no-store",
    }
    try:
        protected = check_http(
            "admin",
            f"{base}/admin",
            minimum_body_bytes=100,
            expected_content_type="text/html",
            required_headers=required,
        )
        weak = check_http(
            "admin",
            f"{base}/weak-admin",
            minimum_body_bytes=100,
            expected_content_type="text/html",
            required_headers=required,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert protected.ok
    assert not weak.ok
    assert "missing header" in weak.detail


def test_report_is_fail_closed_on_critical_failure():
    report = build_report(
        "live",
        [
            check_env_exact("env", "production", "production"),
            check_env_exact("seed", "true", "false"),
        ],
    )
    assert report["go"] is False
    assert report["summary"]["critical_failed"] == 1
