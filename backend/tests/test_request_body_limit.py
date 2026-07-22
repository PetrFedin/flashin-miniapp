from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.middleware.request_body_limit import RequestBodyLimitMiddleware
from backend.middleware.request_context import RequestContextMiddleware


def _body_limit_app(max_body_bytes: int = 8) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=max_body_bytes)
    app.add_middleware(RequestContextMiddleware)

    @app.post("/echo-size")
    async def echo_size(request: Request):
        body = await request.body()
        return {"size": len(body)}

    return app


def test_request_at_limit_is_accepted():
    client = TestClient(_body_limit_app())

    response = client.post(
        "/echo-size",
        content=b"12345678",
        headers={"X-Request-ID": "body-limit-exact"},
    )

    assert response.status_code == 200
    assert response.json() == {"size": 8}
    assert response.headers["X-Request-ID"] == "body-limit-exact"


def test_content_length_over_limit_is_rejected_before_endpoint():
    client = TestClient(_body_limit_app())

    response = client.post(
        "/echo-size",
        content=b"123456789",
        headers={"X-Request-ID": "body-limit-length"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Request body is too large",
        "request_id": "body-limit-length",
    }
    assert response.headers["X-Request-ID"] == "body-limit-length"
    assert response.headers["Cache-Control"] == "no-store"


def test_streamed_body_without_content_length_cannot_bypass_limit():
    client = TestClient(_body_limit_app())

    response = client.post(
        "/echo-size",
        content=iter([b"1234", b"56789"]),
        headers={"X-Request-ID": "body-limit-stream"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Request body is too large",
        "request_id": "body-limit-stream",
    }
    assert response.headers["X-Request-ID"] == "body-limit-stream"
    assert response.headers["Cache-Control"] == "no-store"
