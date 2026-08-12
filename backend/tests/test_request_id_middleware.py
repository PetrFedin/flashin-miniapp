import asyncio
import re

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
