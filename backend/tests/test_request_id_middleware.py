import ast
import asyncio
import re
from pathlib import Path

from backend.middleware.request_id import RequestIdMiddleware, normalize_request_id


def _scope(headers=None):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 443),
    }


def _run(headers=None):
    observed_state = {}

    async def downstream(scope, receive, send):
        observed_state.update(scope.get("state", {}))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(RequestIdMiddleware(downstream)(_scope(headers), receive, send))
    response_headers = dict(sent[0]["headers"])
    return observed_state, response_headers


def test_preserves_safe_incoming_request_id_and_exposes_it_to_downstream():
    state, headers = _run([(b"x-request-id", b"pilot-order-42:refund")])

    assert state["request_id"] == "pilot-order-42:refund"
    assert headers[b"x-request-id"] == b"pilot-order-42:refund"


def test_invalid_request_id_is_not_reflected_and_is_replaced_with_server_id():
    state, headers = _run([(b"x-request-id", b"bad request id\nsecret")])

    generated = state["request_id"]
    assert generated != "bad request id\nsecret"
    assert re.fullmatch(r"[0-9a-f]{32}", generated)
    assert headers[b"x-request-id"] == generated.encode("ascii")


def test_oversized_request_id_is_replaced():
    generated = normalize_request_id("a" * 129)

    assert re.fullmatch(r"[0-9a-f]{32}", generated)


def test_allowed_request_id_charset_is_bounded_and_header_safe():
    value = "A9._:-request-01"

    assert normalize_request_id(value) == value


def test_browser_clients_can_read_request_id_through_cors():
    main_source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8"
    )

    tree = ast.parse(main_source)
    exposed_headers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "expose_headers":
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for item in node.value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                exposed_headers.add(item.value)

    assert "X-Request-ID" in exposed_headers
    assert "X-Trace-ID" in exposed_headers
    assert "app.add_middleware(RequestIdMiddleware)" in main_source


def test_active_trace_is_exposed_as_safe_correlation_header(monkeypatch):
    attributes = {}

    class FakeSpanContext:
        is_valid = True
        trace_id = int("1234567890abcdef1234567890abcdef", 16)

    class FakeSpan:
        def get_span_context(self):
            return FakeSpanContext()

        def is_recording(self):
            return True

        def set_attribute(self, name, value):
            attributes[name] = value

    from backend.middleware import request_id as request_id_module

    monkeypatch.setattr(request_id_module.trace, "get_current_span", lambda: FakeSpan())

    state, headers = _run([(b"x-request-id", b"support-case-17")])

    assert state["request_id"] == "support-case-17"
    assert state["trace_id"] == "1234567890abcdef1234567890abcdef"
    assert headers[b"x-request-id"] == b"support-case-17"
    assert headers[b"x-trace-id"] == b"1234567890abcdef1234567890abcdef"
    assert attributes["flashin.request_id"] == "support-case-17"
