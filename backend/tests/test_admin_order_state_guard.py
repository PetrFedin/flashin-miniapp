import asyncio
import json

from backend.middleware.admin_order_state_guard import AdminOrderStateGuardMiddleware


def _scope(path: str, method: str = "PATCH") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }


def _run(path: str, payload: dict, method: str = "PATCH") -> tuple[list[dict], bytes]:
    received_by_app = bytearray()

    async def downstream(scope, receive, send):
        message = await receive()
        received_by_app.extend(message.get("body", b""))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    body = json.dumps(payload).encode()
    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    middleware = AdminOrderStateGuardMiddleware(downstream)
    asyncio.run(middleware(_scope(path, method), receive, send))
    return sent, bytes(received_by_app)


def test_blocks_paid_status_on_generic_admin_order_patch():
    sent, forwarded = _run("/api/admin/orders/42", {"status": "paid"})

    assert sent[0]["status"] == 409
    assert forwarded == b""


def test_blocks_refund_workflow_statuses():
    for status in ("payment_created", "refund_requested", "refunded"):
        sent, forwarded = _run("/api/admin/orders/42", {"status": status})
        assert sent[0]["status"] == 409
        assert forwarded == b""


def test_allows_operational_status_and_replays_body():
    payload = {"status": "assembling", "tracking_number": "TRACK-1"}
    sent, forwarded = _run("/api/admin/orders/42", payload)

    assert sent[0]["status"] == 204
    assert json.loads(forwarded) == payload


def test_does_not_guard_other_routes():
    payload = {"status": "paid"}
    sent, forwarded = _run("/api/orders/42", payload)

    assert sent[0]["status"] == 204
    assert json.loads(forwarded) == payload
