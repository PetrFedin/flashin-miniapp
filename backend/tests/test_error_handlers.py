from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.error_handlers import register_error_handlers
from backend.middleware.request_context import RequestContextMiddleware


class _Credentials(BaseModel):
    username: str
    password: int


def _error_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/unauthorized")
    def unauthorized():
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.post("/credentials")
    def credentials(payload: _Credentials):
        return payload

    @app.get("/failure")
    def failure():
        raise RuntimeError("database password=must-not-leak")

    return app


def _assert_request_id_contract(response, expected_request_id: str) -> None:
    assert response.headers["X-Request-ID"] == expected_request_id
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["request_id"] == expected_request_id


def test_http_error_preserves_status_detail_headers_and_request_id():
    client = TestClient(_error_app())

    response = client.get(
        "/unauthorized",
        headers={"X-Request-ID": "gateway-auth-401"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    _assert_request_id_contract(response, "gateway-auth-401")


def test_not_found_uses_public_error_contract():
    client = TestClient(_error_app())

    response = client.get(
        "/does-not-exist",
        headers={"X-Request-ID": "gateway-not-found"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
    _assert_request_id_contract(response, "gateway-not-found")


def test_validation_error_does_not_echo_submitted_secrets():
    client = TestClient(_error_app())
    submitted_secret = "super-secret-password"

    response = client.post(
        "/credentials",
        json={"username": "customer", "password": submitted_secret},
        headers={"X-Request-ID": "gateway-validation"},
    )

    assert response.status_code == 422
    body = response.json()
    assert submitted_secret not in response.text
    assert body["detail"]
    assert set(body["detail"][0]).issubset({"loc", "msg", "type"})
    _assert_request_id_contract(response, "gateway-validation")


def test_unhandled_exception_returns_safe_correlated_response():
    client = TestClient(_error_app(), raise_server_exceptions=False)

    response = client.get(
        "/failure",
        headers={"X-Request-ID": "gateway-failure-500"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert "must-not-leak" not in response.text
    _assert_request_id_contract(response, "gateway-failure-500")
